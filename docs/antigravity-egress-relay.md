# Antigravity 出口中继

当执行主机的 Google 请求间歇性返回 403，而本机出口可正常使用时，可以把 Antigravity 的请求经本机转发。此方法需要两台机器同时在线，是一条可选网络路径，并不能确认 Google 拒绝请求的根因。

在有可用出口的本机运行 `bin/agy-egress-relay.py --port 18743`，再从本机建立 SSH 反向转发：

```sh
ssh -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=2 -R 127.0.0.1:18744:127.0.0.1:18743 <executor-host>
```

执行主机将 `bin/agy-via-relay` 配置为 Antigravity 的可执行文件。它只为 Antigravity 子进程设置 `HTTPS_PROXY`；转发端口不可连接时使用原有路径并输出提示。`OPENSWARM_AGY_REAL_BIN` 可指定真实 CLI，默认为 `~/.local/bin/agy`。中继只监听回环地址，并仅接收 Google API 和账号域名的 HTTPS CONNECT；它不读取模型内容或凭据。

长期使用时，将中继和 SSH 转发交给本机的服务管理器监督，并先验证机器重启后的连接、真实任务和全局名额释放。中继或本机离线时，任务可能回到原先间歇出现 403 的路径；不要把降级路径当作问题已解决。其他执行器、代理节点和系统代理设置无需改变。
