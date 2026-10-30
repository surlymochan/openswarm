# Antigravity 出口中继

当执行主机的 Google 请求间歇性返回 403，而本机出口可正常使用时，可以把 Antigravity 的请求经本机转发。此方法需要两台机器同时在线，是一条可选网络路径，并不能确认 Google 拒绝请求的根因。

在两台机器的受限目录中分别放置同一个 URL 安全随机 token 文件（权限 0600，例如以 `secrets.token_urlsafe(32)` 生成），然后在有可用出口的本机运行 `bin/agy-egress-relay.py --port 18743 --token-file <token-file>`，再从本机建立 SSH 反向转发：

```sh
ssh -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=2 -R 127.0.0.1:18744:127.0.0.1:18743 <executor-host>
```

执行主机将 `bin/agy-via-relay` 配置为 Antigravity 的可执行文件；默认 token 文件为 `~/Library/Application Support/openswarm-agy-relay/token`，可通过 `OPENSWARM_AGY_RELAY_TOKEN_FILE` 指定。它先通过中继检查 Google HTTPS 连接，再只为 Antigravity 子进程设置 `HTTPS_PROXY`；连接失败时保留原有网络设置并输出提示。`OPENSWARM_AGY_REAL_BIN` 可指定真实 CLI，默认为 `~/.local/bin/agy`。中继只监听回环地址，验证 token，限制同时连接数，并仅接收 Google API 和账号域名的 HTTPS CONNECT；它不解密模型内容或账号凭据。

token 能阻止未持有 token 的调用者，但文件权限不能隔离运行在同一个操作系统账号下、能够读取该文件或进程环境的其他程序。启用前必须验证其他执行器无法读取 token 或访问持有 token 的环境；如果做不到，不应将该中继视为安全隔离。

长期使用时，将中继和 SSH 转发交给本机的服务管理器监督，并先验证机器重启后的连接、真实任务和全局名额释放。中继或本机离线时，任务可能回到原先间歇出现 403 的路径；不要把降级路径当作问题已解决。其他执行器、代理节点和系统代理设置无需改变。
