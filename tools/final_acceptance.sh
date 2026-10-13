#!/usr/bin/env bash
# AI 视觉质检系统 —— 最终验收脚本
# 用法：sudo bash tools/final_acceptance.sh
IP=192.168.88.130
pass=0; fail=0

chk() {
  if [ "$2" = "$3" ]; then
    echo "  [PASS] $1"; pass=$((pass+1))
  else
    echo "  [FAIL] $1 (期望[$3] 实际[$2])"; fail=$((fail+1))
  fi
}

jget() { python3 -c "import sys,json;d=json.load(sys.stdin);print(d$1)"; }

echo "============ AI 视觉质检系统 · 最终验收 ============"
echo

echo "[验收 1] GET /health 返回 ok"
v=$(curl -sS "http://$IP/health" | grep -o '"status":"ok"' | head -1)
chk "/health 状态为 ok" "$v" '"status":"ok"'
echo

echo "[验收 2] 上传图片返回 缺陷 / 正常 JSON"
ID_D=$(curl -sS -X POST -F file=@/home/chen/ai-inspect/testdata/defect01.jpg "http://$IP/upload" | jget '["data"]["id"]')
v=$(curl -sS "http://$IP/records?limit=1000" | jget '["data"][0]["result"]')
chk "上传 defect01.jpg 判定为 缺陷 (id=$ID_D)" "$v" "缺陷"
ID_N=$(curl -sS -X POST -F file=@/home/chen/ai-inspect/testdata/normal02.jpg "http://$IP/upload" | jget '["data"]["id"]')
v=$(curl -sS "http://$IP/records?limit=1000" | jget '["data"][0]["result"]')
chk "上传 normal02.jpg 判定为 正常 (id=$ID_N)" "$v" "正常"
v=$(curl -sS "http://$IP/records?limit=1000" | jget '["data"][0]["confidence"]')
echo "  最新一条记录置信度 = $v"
CLEAN_IDS="$ID_D $ID_N"
echo

echo "[验收 3] GET /records 能查询到质检记录"
v=$(curl -sS "http://$IP/records" | jget '["total"]')
if [ "$v" -ge 6 ] 2>/dev/null; then chk "/records 记录数 >= 6" "yes" "yes"; else chk "/records 记录数 >= 6" "no($v)" "yes"; fi
v=$(curl -sS "http://$IP/records" | jget '["defect"]')
echo "  缺陷 $v 条 / 正常 $(curl -sS http://$IP/records | jget '["normal"]') 条"
echo

echo "[验收 4] 浏览器可访问静态原图"
v=$(curl -sS -o /dev/null -w '%{http_code}' "http://$IP/static/defect01.jpg")
chk "/static/defect01.jpg 返回 200" "$v" "200"
v=$(curl -sS -o /dev/null -w '%{content_type}' "http://$IP/static/defect01.jpg")
chk "响应类型为 image/jpeg" "$v" "image/jpeg"
echo

echo "[验收 5] 三个容器全部运行且在 ai-net 网络"
v=$(docker ps --format '{{.Names}}' | sort | tr '\n' ',' | sed 's/,$//')
chk "运行中的容器" "$v" "fastapi-ai,mysql-ai,nginx-ai"
v=$(docker network inspect ai-net --format '{{range .Containers}}{{.Name}} {{end}}' | wc -w)
chk "ai-net 网络内容器数 = 3" "$v" "3"
echo

echo "[加分项] Nginx 加固与扩展能力"
curl -sS --max-time 3 -o /dev/null "http://$IP:8000/health" 2>/dev/null
rc=$?
if [ $rc -ne 0 ]; then v="不可达"; else v="可达"; fi
chk "后端 8000 端口对外不可达（已隐藏）" "$v" "不可达"
v=$(curl -sS "http://$IP/api/health" | grep -o '"status":"ok"' | head -1)
chk "/api/health 前缀重写转发正常" "$v" '"status":"ok"'
v=$(curl -sS "http://$IP/nginx-health" | tr -d '\n')
chk "/nginx-health 直连检查" "$v" "nginx ok"
echo

# 清理本次验收插入的测试记录与图片，避免污染验收数据
for id in $CLEAN_IDS; do
  f=$(docker exec -e MYSQL_PWD=123456 mysql-ai mysql -uroot ai_quality -N \
        -e "SELECT filename FROM inspect_records WHERE id=$id;" 2>/dev/null)
  [ -n "$f" ] && rm -f "/home/chen/ai-inspect/upload_img/$f"
  docker exec -e MYSQL_PWD=123456 mysql-ai mysql -uroot ai_quality \
        -e "DELETE FROM inspect_records WHERE id=$id;" 2>/dev/null
done
echo "[清理] 已移除本次验收产生的临时记录 id: $CLEAN_IDS"
echo

echo "============ 验收结果：$pass 通过 / $fail 失败 ============"
[ "$fail" -eq 0 ] && echo ">>> 全部验收项通过，项目可交付。" || echo ">>> 存在未通过项，请排查。"
exit $fail
