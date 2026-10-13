# -*- coding: utf-8 -*-
"""
AI 视觉质检后端服务（FastAPI）
=====================================================================
技术栈：FastAPI + PyMySQL + Pillow + NumPy
职责：
  1. 接收工业相机上传的产品外观图片（multipart/form-data）
  2. 调用质检推理引擎，输出「缺陷 / 正常」判定结果 + 置信度
  3. 将 图片名称 / 质检结果 / 置信度 / 时间 写入 MySQL（容器名 mysql-ai）
  4. 对外提供 /health、/upload、/records 接口
  5. 通过 /static/** 暴露原图，可在浏览器直接查看

注意：数据库 host 必须写容器名 mysql-ai（同一个 ai-net 网络内，
容器名就是域名），不能写 localhost —— localhost 指向的是本容器自身。
"""
import datetime
import io
import logging
import os
import re
import time

import numpy as np
import pymysql
from PIL import Image, ImageOps, UnidentifiedImageError
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymysql.cursors import DictCursor

# =====================================================================
# 一、配置区（全部支持环境变量覆盖，方便容器化部署）
# =====================================================================
DB_HOST = os.getenv("DB_HOST", "mysql-ai")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "aiuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "aiuser123")
DB_NAME = os.getenv("DB_NAME", "ai_quality")

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/app/upload_img")
STATIC_URL = os.getenv("STATIC_URL", "/static")
MAX_ANALYZE_SIDE = int(os.getenv("MAX_ANALYZE_SIDE", "1024"))
DB_WAIT_RETRIES = int(os.getenv("DB_WAIT_RETRIES", "30"))   # 设为 0 可跳过启动等待（离线自检用）
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# 北京时间（不依赖容器 tzdata，直接按 UTC+8 计算，避免时区跑偏）
CST = datetime.timezone(datetime.timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ai-inspect")

os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(
    title="AI 视觉质检服务",
    description="工厂生产线产品外观视觉质检：上传图片 -> 模型推理 -> 结果入库",
    version="1.0.0",
)

# 挂载静态目录：浏览器可直接访问 /static/xxx.jpg 查看原图
app.mount("/static", StaticFiles(directory=UPLOAD_DIR), name="static")


# =====================================================================
# 二、数据库层
# =====================================================================
DDL = """
CREATE TABLE IF NOT EXISTS inspect_records (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键，自增',
  filename    VARCHAR(255) NOT NULL               COMMENT '上传的图片文件名',
  result      VARCHAR(16)  NOT NULL               COMMENT '质检结果：缺陷 / 正常',
  confidence  DECIMAL(5,4) NOT NULL               COMMENT '置信度，0.0000 ~ 1.0000',
  defect_type VARCHAR(64)  DEFAULT NULL           COMMENT '缺陷类型，正常时为 normal',
  image_url   VARCHAR(512) DEFAULT NULL           COMMENT '图片访问地址（/static/xxx.jpg）',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '质检时间',
  PRIMARY KEY (id),
  KEY idx_created_at (created_at),
  KEY idx_result (result)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='产品外观质检历史记录表';
"""


def get_conn():
    """每次请求新建连接。生产环境应换连接池，实训场景够用且不易踩坑。"""
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
        connect_timeout=5,
    )


def wait_for_db(retries: int = 30, interval: float = 2.0) -> bool:
    """容器启动顺序不定：MySQL 可能比本服务晚就绪，这里做启动等待重试。"""
    if retries <= 0:
        return False
    last_err = None
    for i in range(1, retries + 1):
        try:
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(DDL)
                return True
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.warning("等待 MySQL 就绪（第 %d/%d 次）：%s", i, retries, exc)
            time.sleep(interval)
    log.error("MySQL 连接失败：%s", last_err)
    return False


def insert_record(filename, result, confidence, defect_type, image_url) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO inspect_records
                       (filename, result, confidence, defect_type, image_url, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (filename, result, float(confidence), defect_type, image_url,
                 datetime.datetime.now(CST).replace(tzinfo=None)),
            )
            return cur.lastrowid
    finally:
        conn.close()


def query_records(limit: int, offset: int, result_filter: str = None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            where, params = "", []
            if result_filter:
                where = " WHERE result = %s"
                params.append(result_filter)
            cur.execute("SELECT COUNT(*) AS c FROM inspect_records" + where, params)
            total = cur.fetchone()["c"]
            cur.execute(
                """SELECT id, filename, result, confidence, defect_type,
                          image_url, created_at
                     FROM inspect_records""" + where +
                " ORDER BY id DESC LIMIT %s OFFSET %s",
                params + [limit, offset],
            )
            rows = cur.fetchall()
            cur.execute(
                """SELECT result, COUNT(*) AS c FROM inspect_records
                    GROUP BY result"""
            )
            counter = {r["result"]: r["c"] for r in cur.fetchall()}
        return total, rows, counter
    finally:
        conn.close()


# =====================================================================
# 三、质检推理引擎（模拟 AI 模型）
# =====================================================================
# 说明：真实产线会用 YOLO / ResNet 之类的 CNN 权重做推理。实训环境不引入
# 深度学习框架，这里用一个「可解释的灰度统计模型」替代，同一套接口签名，
# 后续把 run_inference() 换成真实模型即可，业务代码不用改。
#
# 提取 4 个特征（全部归一化到 0~1）：
#   f1 过暗面积占比      —— 产品发黑 / 严重污损
#   f2 过曝面积占比      —— 反光 / 曝光异常
#   f3 局部异常密度      —— 划痕、杂质、脏点
#   f4 低对比度程度      —— 成像模糊、失焦
# 加权求和得到缺陷分 score，再用 Sigmoid 压缩成概率，更有「模型味」。

W_DARK, W_BRIGHT, W_SCRATCH, W_BLUR = 0.90, 0.55, 0.85, 0.70
DARK_TH, BRIGHT_TH, SCRATCH_TH = 0.24, 0.96, 0.25
BLUR_REF = 0.04          # 灰度标准差低于该值视为「低对比/失焦」
SCRATCH_NORM = 0.005     # 局部异常点占比达到 0.5% 即视为「满格」


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-x))


def extract_features(img: Image.Image) -> dict:
    """把图片转成灰度矩阵并提取特征。先缩放，控制内存与推理耗时。"""
    img = ImageOps.exif_transpose(img).convert("L")
    if max(img.size) > MAX_ANALYZE_SIDE:
        ratio = MAX_ANALYZE_SIDE / max(img.size)
        img = img.resize(
            (max(1, int(img.width * ratio)), max(1, int(img.height * ratio))),
            Image.BILINEAR,
        )

    g = np.asarray(img, dtype=np.float32) / 255.0

    dark_ratio = float(np.mean(g < DARK_TH))
    bright_ratio = float(np.mean(g > BRIGHT_TH))

    # 3x3 均值滤波，与原图做差 -> 局部突变点 = 划痕 / 杂质
    pad = np.pad(g, 1, mode="edge")
    local_sum = np.zeros_like(g)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            local_sum += pad[dy:dy + g.shape[0], dx:dx + g.shape[1]]
    local_mean = local_sum / 9.0
    anomaly_ratio = float(np.mean(np.abs(g - local_mean) > SCRATCH_TH))

    contrast = float(g.std())
    blur_score = float(min(1.0, max(0.0, (BLUR_REF - contrast) / BLUR_REF)))

    return {
        "dark_ratio": round(dark_ratio, 4),
        "bright_ratio": round(bright_ratio, 4),
        "anomaly_ratio": round(anomaly_ratio, 4),
        "contrast": round(contrast, 4),
        "blur_score": round(blur_score, 4),
        "image_size": "%dx%d" % (img.width, img.height),
        # 特征归一化：0.5% 的局部异常点就算「满格」
        "f3_scratch": round(min(1.0, anomaly_ratio / SCRATCH_NORM), 4),
    }


def run_inference(data: bytes) -> dict:
    """质检推理主入口：返回判定结果、置信度、缺陷类型与特征明细。"""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except UnidentifiedImageError:
        raise ValueError("无法识别的图片格式，请上传 jpg/png/bmp/webp 图片")
    except Exception as exc:  # noqa: BLE001
        raise ValueError("图片解析失败：%s" % exc)

    feats = extract_features(img)

    # 四项特征的加权贡献（各自先归一化到 0~1）
    c_dark = W_DARK * min(1.0, feats["dark_ratio"] * 1.2)
    c_bright = W_BRIGHT * min(1.0, feats["bright_ratio"] * 1.5)
    c_scratch = W_SCRATCH * feats["f3_scratch"]
    c_blur = W_BLUR * feats["blur_score"]

    score = min(1.0, max(0.0, c_dark + c_bright + c_scratch + c_blur))

    p_defect = float(_sigmoid(8.0 * (score - 0.5)))

    if p_defect >= 0.5:
        result, confidence = "缺陷", p_defect
    else:
        result, confidence = "正常", 1.0 - p_defect

    # 找出贡献最大的特征，作为缺陷类型描述
    contributors = {
        "亮度异常(过暗/污损)": c_dark,
        "过曝/反光":          c_bright,
        "表面划痕/杂质":      c_scratch,
        "成像模糊/低对比":    c_blur,
    }
    top = max(contributors, key=contributors.get)
    if result == "正常" or contributors[top] < 0.05:
        defect_type = "normal"
        defect_type_cn = "无异常"
    else:
        defect_type = top
        defect_type_cn = top

    return {
        "result": result,
        "confidence": round(float(confidence), 4),
        "defect_score": round(score, 4),
        "defect_type": defect_type,
        "defect_type_cn": defect_type_cn,
        "features": feats,
    }


# =====================================================================
# 四、工具函数
# =====================================================================
def safe_filename(name: str) -> str:
    """防止路径穿越：只保留文件名部分，过滤非法字符。"""
    name = os.path.basename(name or "").strip()
    name = re.sub(r"[^\w.\-\u4e00-\u9fa5]", "_", name)
    if not name or name in (".", ".."):
        name = "upload_%d.jpg" % int(time.time())
    return name[:120]


def unique_path(filename: str) -> str:
    """同名文件自动加序号，保证首张图片保留原始文件名（便于 /static/xxx.jpg 访问）。"""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(UPLOAD_DIR, filename)
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(UPLOAD_DIR, "%s_%d%s" % (base, i, ext))
        i += 1
    return candidate


def fmt_row(row: dict) -> dict:
    row = dict(row)
    if isinstance(row.get("created_at"), datetime.datetime):
        row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    if row.get("confidence") is not None:
        row["confidence"] = float(row["confidence"])
    return row


# =====================================================================
# 五、接口层
# =====================================================================
# 启动时等待 MySQL 就绪并建表（容器启动顺序不确定，必须重试）
_DB_READY = wait_for_db(retries=DB_WAIT_RETRIES)
log.info("数据库初始化：%s", "成功" if _DB_READY else "跳过/失败")
log.info("上传目录：%s", UPLOAD_DIR)


@app.get("/health", summary="健康检查")
def health():
    """验收项 1：curl http://<IP>/health 返回 ok"""
    db_state = "connected"
    try:
        conn = get_conn()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        db_state = "error: %s" % exc
    return {
        "status": "ok",
        "service": "fastapi-ai",
        "db": db_state,
        "time": datetime.datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.post("/upload", summary="上传图片并质检")
async def upload(file: UploadFile = File(...)):
    """验收项 2：上传图片，返回质检 JSON（缺陷 / 正常）"""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")

    filename = safe_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext and ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="不支持的图片格式：%s" % ext)

    t0 = time.time()
    try:
        infer = run_inference(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    cost_ms = int((time.time() - t0) * 1000)

    save_path = unique_path(filename)
    with open(save_path, "wb") as f:
        f.write(raw)
    saved_name = os.path.basename(save_path)
    image_url = "%s/%s" % (STATIC_URL, saved_name)

    try:
        rid = insert_record(saved_name, infer["result"], infer["confidence"],
                            infer["defect_type"], image_url)
    except Exception as exc:  # noqa: BLE001
        log.exception("写入数据库失败")
        raise HTTPException(status_code=503, detail="数据库写入失败：%s" % exc)

    return {
        "code": 0,
        "msg": "质检完成",
        "data": {
            "id": rid,
            "filename": saved_name,
            "result": infer["result"],
            "confidence": infer["confidence"],
            "defect_type": infer["defect_type"],
            "defect_type_cn": infer["defect_type_cn"],
            "defect_score": infer["defect_score"],
            "features": infer["features"],
            "image_url": image_url,
            "infer_cost_ms": cost_ms,
        },
    }


@app.get("/records", summary="查询全部质检历史记录")
def records(
    limit: int = Query(100, ge=1, le=1000, description="每页条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
    result: str = Query(None, description="按结果过滤：缺陷 / 正常"),
):
    """验收项 3：curl http://<IP>/records 查到质检记录"""
    try:
        total, rows, counter = query_records(limit, offset, result)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="数据库查询失败：%s" % exc)
    return {
        "code": 0,
        "msg": "ok",
        "total": total,
        "defect": counter.get("缺陷", 0),
        "normal": counter.get("正常", 0),
        "count": len(rows),
        "data": [fmt_row(r) for r in rows],
    }


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def console():
    """一个极简的网页质检控制台，方便浏览器演示整条链路。"""
    return CONSOLE_HTML


CONSOLE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 视觉质检控制台</title>
<style>
  :root{--bg:#f5f7fa;--card:#fff;--line:#e3e8ef;--txt:#1f2937;--sub:#6b7280;
        --red:#d93025;--green:#0f9d58;--blue:#2563eb;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  .wrap{max-width:1080px;margin:0 auto;padding:28px 20px 60px}
  h1{font-size:22px;margin:0 0 4px}
  .sub{color:var(--sub);margin-bottom:22px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;
        padding:20px;margin-bottom:18px}
  .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  input[type=file]{flex:1;min-width:240px;padding:8px;border:1px dashed var(--line);
                   border-radius:8px;background:#fafbfc}
  button{background:var(--blue);color:#fff;border:0;border-radius:8px;
         padding:10px 20px;font-size:14px;cursor:pointer}
  button:hover{opacity:.9}
  .stat{display:flex;gap:24px;flex-wrap:wrap;margin-top:14px}
  .stat div{color:var(--sub)}
  .stat b{display:block;font-size:22px;color:var(--txt)}
  pre{background:#0f172a;color:#e2e8f0;padding:14px;border-radius:8px;
      overflow:auto;font-size:12.5px;max-height:280px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left}
  th{background:#f8fafc;color:var(--sub);font-weight:600}
  .tag{padding:2px 9px;border-radius:20px;font-size:12px;color:#fff}
  .t-defect{background:var(--red)} .t-normal{background:var(--green)}
  img.thumb{width:52px;height:52px;object-fit:cover;border-radius:6px;
            border:1px solid var(--line);cursor:zoom-in}
</style>
</head>
<body>
<div class="wrap">
  <h1>AI 视觉质检控制台</h1>
  <div class="sub">Nginx 统一入口 &rarr; FastAPI 推理服务 &rarr; MySQL 结果落库</div>

  <div class="card">
    <div class="row">
      <input type="file" id="f" accept="image/*">
      <button onclick="doUpload()">上传并质检</button>
      <button onclick="loadRecords()" style="background:#6b7280">刷新记录</button>
    </div>
    <div class="stat">
      <div>总记录<b id="s-total">-</b></div>
      <div>缺陷<b id="s-defect" style="color:#d93025">-</b></div>
      <div>正常<b id="s-normal" style="color:#0f9d58">-</b></div>
    </div>
    <pre id="out" style="display:none"></pre>
  </div>

  <div class="card">
    <table>
      <thead><tr><th>ID</th><th>图片</th><th>文件名</th><th>结果</th>
      <th>置信度</th><th>缺陷类型</th><th>时间</th></tr></thead>
      <tbody id="tb"><tr><td colspan="7" style="color:#9ca3af">加载中…</td></tr></tbody>
    </table>
  </div>
</div>
<script>
async function doUpload(){
  const f = document.getElementById('f').files[0];
  if(!f){ alert('请先选择一张图片'); return; }
  const fd = new FormData(); fd.append('file', f);
  const out = document.getElementById('out');
  out.style.display='block'; out.textContent='推理中…';
  try{
    const r = await fetch('/upload',{method:'POST',body:fd});
    out.textContent = JSON.stringify(await r.json(), null, 2);
    loadRecords();
  }catch(e){ out.textContent = '请求失败：'+e; }
}
async function loadRecords(){
  try{
    const r = await fetch('/records?limit=20');
    const j = await r.json();
    document.getElementById('s-total').textContent  = j.total;
    document.getElementById('s-defect').textContent = j.defect;
    document.getElementById('s-normal').textContent = j.normal;
    const tb = document.getElementById('tb');
    if(!j.data.length){ tb.innerHTML='<tr><td colspan="7" style="color:#9ca3af">暂无记录</td></tr>'; return; }
    tb.innerHTML = j.data.map(function(d){
      var cls = d.result==='缺陷' ? 't-defect' : 't-normal';
      return '<tr><td>'+d.id+'</td>'+
        '<td><img class="thumb" src="'+d.image_url+'" onclick="window.open(this.src)"></td>'+
        '<td>'+d.filename+'</td>'+
        '<td><span class="tag '+cls+'">'+d.result+'</span></td>'+
        '<td>'+Number(d.confidence).toFixed(4)+'</td>'+
        '<td>'+(d.defect_type||'')+'</td>'+
        '<td>'+d.created_at+'</td></tr>';
    }).join('');
  }catch(e){ console.error(e); }
}
loadRecords();
</script>
</body>
</html>
"""
