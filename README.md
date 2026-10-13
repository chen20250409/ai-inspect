# AI 视觉质检系统（Docker + FastAPI + MySQL + Nginx）

工厂生产线产品外观视觉质检实训项目。工业相机上传产品图片 → AI 模型推理判定
「缺陷 / 正常」并输出置信度 → 结果写入 MySQL → 通过统一入口查询历史记录、
在线查看原图。

## 一、系统架构

```
                       ai-net（自定义桥接网络）
   ┌─────────────────────────────────────────────────────────────┐
   │                                                             │
浏览器 ──:80──►  nginx-ai  ──────►  fastapi-ai  ──────►  mysql-ai  │
   │            (反向代理)          (质检推理)          (结果落库)  │
   │                │                   │                   │      │
   │                │  /static/ 直接读共享卷                 │      │
   │                └───────────────────┘                   │      │
   └─────────────────────────────────────────────────────────────┘
        upload_img/ (宿主机目录)          mysql-data/ (宿主机目录)
```

| 容器 | 镜像 | 对外端口 | 职责 |
|------|------|----------|------|
| `nginx-ai` | nginx:latest | **80** | 统一入口、反向代理、静态图片直出 |
| `fastapi-ai` | fastapi-ai:v1（自制） | 无（仅内网 8000） | 接收图片、质检推理、结果写库 |
| `mysql-ai` | mysql:8.0 | 3306 | 质检记录持久化 |

关键点：`fastapi-ai` **不映射任何宿主机端口**，8000 端口只存在于 `ai-net` 网络
内部，外部只能通过 Nginx 的 80 端口访问。

## 二、目录结构

```
ai-inspect/
├── backend/                 # FastAPI 后端
│   ├── main.py              # 接口 + 质检推理引擎
│   ├── requirements.txt     # 依赖清单（版本锁定）
│   ├── Dockerfile           # 自制镜像
│   └── .dockerignore
├── db/
│   └── init.sql             # 建库、建表、建应用账号
├── nginx/
│   └── nginx.conf           # 反向代理配置
├── tools/
│   ├── gen_test_images.py   # 生成 6 张测试图片（纯 PIL）
│   ├── self_check.py        # 离线自检：验证 6 张图的判定结果
│   └── capture_screenshots.py  # SSH 隧道 + 无头浏览器自动截图
├── screenshots/             # 功能验收截图
├── testdata/                # 测试图片
├── upload_img/              # 上传图片持久化目录（挂载到容器）
├── mysql-data/              # MySQL 数据持久化目录（挂载到容器）
├── .gitignore
└── README.md
```

## 三、接口说明

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查，返回 `{"status":"ok"}`，同时探测数据库连通性 |
| POST | `/upload` | 上传图片（字段名 `file`），返回质检结果 JSON |
| GET | `/records` | 查询全部质检记录，支持 `limit` / `offset` / `result` 过滤 |
| GET | `/static/{filename}` | 查看上传的原图，由 Nginx 直接返回 |
| GET | `/` | 网页版质检控制台（上传 + 记录查看） |
| GET | `/docs` | FastAPI 自动生成的接口文档 |
| GET | `/api/**` | 等价于去掉 `/api` 前缀的接口（Nginx 重写） |
| GET | `/nginx-health` | Nginx 自身健康检查，不经过后端 |

### 质检结果字段

```json
{
  "code": 0,
  "msg": "质检完成",
  "data": {
    "id": 2,
    "filename": "defect01.jpg",
    "result": "缺陷",
    "confidence": 0.982,
    "defect_type": "亮度异常(过暗/污损)",
    "defect_score": 1.0,
    "features": { "...": "特征明细，便于排错溯源" },
    "image_url": "/static/defect01.jpg",
    "infer_cost_ms": 19
  }
}
```

## 四、质检推理引擎

实训环境不引入 PyTorch / YOLO 等重型框架，改用一个**可解释的灰度统计模型**：
把图片转灰度后提取 4 个归一化特征，加权求和得到缺陷分，再用 Sigmoid 压缩成概率。

| 特征 | 含义 | 权重 |
|------|------|------|
| f1 过暗面积占比 | 产品发黑、镜头遮挡、严重污损 | 0.90 |
| f2 过曝面积占比 | 强反光、曝光异常 | 0.55 |
| f3 局部异常密度 | 划痕、杂质、脏点（3×3 均值滤波做差） | 0.85 |
| f4 低对比度程度 | 成像模糊、失焦 | 0.70 |

- 缺陷分 `score = Σ(权重 × 归一化特征)`，裁剪到 `[0, 1]`
- 缺陷概率 `p = sigmoid(8 × (score - 0.5))`
- `p ≥ 0.5` 判为「缺陷」，置信度取 `p`；否则判为「正常」，置信度取 `1 - p`
- 贡献权重最大的特征即为缺陷类型

接口签名与真实模型一致，后续把 `run_inference()` 换成 CNN 推理即可，业务代码无需改动。

## 五、部署步骤

```bash
# 0. 创建网络
sudo docker network create ai-net

# 1. 启动 MySQL
sudo docker run -d --name mysql-ai --network ai-net \
  -e MYSQL_ROOT_PASSWORD=123456 -e TZ=Asia/Shanghai -p 3306:3306 \
  -v $PWD/mysql-data:/var/lib/mysql \
  mysql:8.0 --character-set-server=utf8mb4 \
            --collation-server=utf8mb4_unicode_ci --default-time-zone=+08:00

# 2. 建库建表
sudo docker exec -i mysql-ai mysql -uroot -p123456 < db/init.sql

# 3. 构建并启动后端
cd backend && sudo docker build -t fastapi-ai:v1 .
sudo docker run -d --name fastapi-ai --network ai-net \
  -v $PWD/../upload_img:/app/upload_img \
  -e DB_HOST=mysql-ai -e DB_USER=aiuser -e DB_PASSWORD=aiuser123 \
  -e DB_NAME=ai_quality fastapi-ai:v1

# 4. 启动 Nginx
sudo docker run -d --name nginx-ai --network ai-net -p 80:80 \
  -v $PWD/nginx/nginx.conf:/etc/nginx/nginx.conf:ro \
  -v $PWD/upload_img:/usr/share/nginx/html/static:ro nginx:latest
```

## 六、验收命令

```bash
IP=192.168.88.130

curl http://$IP/health                      # {"status":"ok",...}
curl -X POST -F "file=@testdata/defect01.jpg" http://$IP/upload
curl -X POST -F "file=@testdata/normal01.jpg" http://$IP/upload
curl http://$IP/records                     # 查到质检记录

# 浏览器打开： http://$IP/static/defect01.jpg
# 浏览器打开： http://$IP/          （质检控制台）
```

离线自检（不依赖数据库）：

```bash
python3 tools/gen_test_images.py   # 生成测试图片
python3 tools/self_check.py        # 6 张图判定结果自检
```

## 七、注意事项

- **数据库 host 必须写容器名 `mysql-ai`**，同一个自定义网络内容器名即域名；
  写 `localhost` 会指向应用容器自己，直接连接失败。
- **Nginx 的 `client_max_body_size`** 默认只有 1M，上传大图会返回 413，已在配置中放开到 20m。
- **Nginx 对 `upstream` 的静态解析**：容器重建后 IP 变化会导致 502，本项目改用
  `resolver 127.0.0.11` + 变量 `proxy_pass` 实现运行时动态解析。
- **容器时区**：基础镜像默认 UTC，会导致 `created_at` 比北京时间少 8 小时，
  已通过 `TZ` 环境变量与 `--default-time-zone=+08:00` 修正。
