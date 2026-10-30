# 本机 Pi 到私有 MiniMax broker

`bin/pi-local-http-bridge.py` 在本机回环地址接收 Pi 的 Anthropic Messages 请求，通过 SSH 将请求转交给 gocc 上已有的 `pi-broker-stdio`。上游 MiniMax 凭据仍在 broker 主机；桥接只读取单独的本机随机访问令牌。

准备一个仅本人可读的令牌文件（建议目录权限 0700、文件权限 0600），在 Pi 的独立 `models.json` 中为 `minimax-sandbox` 使用相同令牌和 `http://127.0.0.1:8742`，并仅在受全局协调器约束的 OpenBot 或 OpenSwarm 执行路径使用。用户可以通过系统服务管理器以 `--token-file <路径>` 持久启动此脚本；默认监听 8742，`--port` 可调整端口。每个请求重新建立 SSH 连接，无需保留长时 SSH 隧道。上游路径仅接受 Messages 及 Pi 所需的 beta 查询，broker 继续验证模型并控制上游并发。

此桥接不替代执行器的全局准入，也不提供 Pi 的操作系统沙箱。直接运行 Pi 必须通过既有协调器申请并释放名额；只读执行仍需实际沙箱或明确禁用工具。不要将本机访问令牌或上游凭据提交到仓库。
