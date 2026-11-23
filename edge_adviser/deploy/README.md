# EdgeAdviser 部署指南

两种部署形态，按现场条件选择：

| 形态 | 适用 | 入口 |
|---|---|---|
| Docker / Compose | 有容器运行时的工控机/网关 | [`docker-compose.yml`](docker-compose.yml) |
| systemd 裸机 | 无 Docker 的老工控机（常见） | [`edge-adviser.service`](edge-adviser.service) |

## 1. 准备配置

```bash
cp edge_adviser/config.sample.json config.json
# 编辑：assets / metrics / sources / providers（在线模型或离线 Ollama）
```

关键项：
- `workspace` —— 所有状态（快照/报告/知识/审计账本）落盘的目录；
  部署时建议用 `EDGE_ADVISER_WORKSPACE` 环境变量覆盖到挂载卷。
- `hmi_pin` —— 局域网暴露时**必须**启用 PIN（默认关闭）。
- `data_freshness_s` / `max_empty_windows` —— 数据可信度门禁阈值。

## 2. Docker / Compose

```bash
cd edge_adviser/deploy
cp ../config.sample.json config.json && vi config.json
docker compose up -d --build
```

- 镜像多阶段构建：Node 编译 webui → slim Python 运行时，运行层无 Node。
- 健康检查用真实 `/api/health`（含调度器心跳）。
- 数据卷：`./data` ↔ 容器 `/data`，升级镜像不丢数据。
- 资源上限默认 1GB 内存，可按设备调整。

## 3. systemd 裸机

```bash
# 一次性安装（建议专用 venv）
python3.11 -m venv /opt/edge-adviser/.venv
/opt/edge-adviser/.venv/bin/pip install -e /path/to/repo/edge_adviser

# 目录约定
sudo mkdir -p /etc/edge-adviser /var/lib/edge-adviser
sudo cp config.json /etc/edge-adviser/
sudo useradd -r -s /usr/sbin/nologin edge-adviser
sudo chown -R edge-adviser /var/lib/edge-adviser

sudo cp edge_adviser/deploy/edge-adviser.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now edge-adviser
journalctl -u edge-adviser -f          # 跟日志
```

## 4. 升级 / 回滚

- Docker：`docker compose build && docker compose up -d`；数据在卷里，回滚即切回旧 tag。
- systemd：`git pull && pip install -e . && systemctl restart edge-adviser`。
- 配置回滚：阈值在线编辑（PATCH）会写回 config.json 并留 runlog 审计行，
  出问题按 runlog 的 `threshold_edit` 记录手工还原。

## 5. 验证清单（上线前 5 分钟）

1. `curl http://<host>:8000/api/health` → `status` 不是 `error`，调度器心跳在跳。
2. 浏览器打开 HMI → 总览有卡片、实时监控曲线在动（SSE 连接灯绿色）。
3. 制造一次断数据（停数据源）→ 连续空窗后资产转 OFFLINE 并出告警 →
   确认告警、填写处理说明 → 反馈台账出现该条记录。
4. 触发一次问答 → 门禁拒绝或正常回答，审计页能看到 `qa` 行。
5. 设置页 → 运行观测卡片显示模型成功率 / MTTA / 磁盘占用。
