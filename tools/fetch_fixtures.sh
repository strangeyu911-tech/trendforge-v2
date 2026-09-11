#!/usr/bin/env bash
# 抓取冒烟测试所需的真实 API 响应（curl 在本机可用，node fetch 被沙箱拦截）
set -u
FX="D:/tmp/fx"
mkdir -p "$FX"
B="http://127.0.0.1:8000/api"
ID="b41a0c5c-053b-4477-aa55-6da25636d530"   # 一条真实内容 ID

sanitize() { echo "$1" | sed 's#https\?://##; s#[^A-Za-z0-9]#_#g'; }

paths=(
  "/health"
  "/markets"
  "/contents?limit=200"
  "/contents/${ID}"
  "/contents/${ID}/trace"
  "/pipeline/tasks?limit=50"
  "/prompts/suggestions?status=pending"
  "/prompts/suggestions?status=adopted"
  "/prompts/suggestions?status=all"
  "/prompts/templates"
  "/prompts/versions?template=writer"
  "/prompts/adoption-impact"
  "/analytics/overview"
  "/analytics/reports"
  "/analytics/center"
  "/kb/stats"
  "/kb/documents"
  "/kb/freshness"
  "/kb/patches"
  "/calibration/samples"
  "/calibration/report"
  "/bad-cases"
)

for p in "${paths[@]}"; do
  key=$(sanitize "${B}${p}")
  code=$(curl -s -m 60 -o "$FX/${key}.json" -w "%{http_code}" "${B}${p}")
  size=$(stat -c %s "$FX/${key}.json" 2>/dev/null || echo 0)
  echo "${code}  ${size}B  ${p}"
done
