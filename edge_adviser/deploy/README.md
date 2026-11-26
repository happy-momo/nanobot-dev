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

## 6. 真实数据源联调（按部署使用的协议执行）

- REST：用只读测试接口验证开始/结束时间参数、响应路径、时间戳和指标字段映射；测试连接通过后，等待至少一个采集周期，确认新快照的数值和时间范围符合设备端响应。
- MQTT：从目标 Broker 发布一条带有效时间戳和配置指标字段的 JSON 消息，确认实时连接测试收到该消息，保存配置后确认调度产生新快照；重启服务后确认重新订阅。当前不保证断线期间的消息补采。
- OPC UA：用部署证书和实际账号验证安全模式、服务端信任链、客户端证书/私钥、NodeId、节点状态码及源时间戳；保存后确认周期读取，重启后再次验证连接。
- Modbus TCP：在目标隔离网络验证 Unit ID、功能区、零基地址、数据类型、字序、缩放和偏移；将读数与设备工程值对照，并确认连续采集和超时/断线后的状态变化。
- 对所有启用凭据的连接器，验证凭据在服务重启后仍可使用，并确认配置 API 响应、日志和删除备份不包含凭据值；确认部署密钥可用且已通过运维密钥管理方式备份。

## 7. TLS 终止（生产必须）

edge-adviser 服务明文 HTTP，安全模型默认"局域网可信 + PIN bearer"。跨网段或
任何不受控链路访问时，必须在前面加 TLS 终止。最小 Caddy 示例（自动证书）：

```caddyfile
adviser.plant.local {
    reverse_proxy 127.0.0.1:8000
}
```

nginx 等价物：

```nginx
server {
    listen 443 ssl;
    server_name adviser.plant.local;
    ssl_certificate     /etc/nginx/certs/adviser.crt;
    ssl_certificate_key /etc/nginx/certs/adviser.key;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 3600s;   # SSE 长连接：/stream/* 不能被默认 60s 截断
        proxy_buffering off;        # SSE 逐事件下发，缓冲会攒批
    }
}
```

两个必须项：`proxy_read_timeout`（SSE 心跳 25s 一次，但 EventSource 重连前的
读超时要大于任务 600s 上限才有意义）与 `proxy_buffering off`（否则告警/任务
事件被攒成一大块下发，HMI 的"实时"变"延时回放"）。如需按主机名收敛访问面，
可在配置中设置 `trusted_hosts`（裸主机名匹配，如 `"plant.local"`，不含端口）。

## 访问令牌的日志取舍

PIN 模式下 EventSource 无法携带 Authorization 头，`/stream/*` 订阅走
`?token=<hmac>` 查询参数。查询参数会进入反代访问日志——这是已知的取舍：
令牌 12h 过期、HMAC 签名不可伪造、且只应出现在受控的运维日志里。若日志
外发第三方聚合，请在反代层剥离 query（nginx: `log_format` 里用
`$request_method $uri` 替代 `$request`）。
