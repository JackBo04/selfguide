# 收发流程

下列命令在服务器运行。`<skill>` 是当前安装目录，`<run>` 是任务目录，`<url>` 必须取自返回文件的 `result.url`，不可猜测。按本轮编号替换 `001`。命令成功后才执行下一步。

新任务在 selfguide 项目自动打开独立窗口。已有任务先读 `session.py status --run <run> --brief`；`open --run` 复用该任务窗口，不能用 `project` 重置会话。

```bash
python <skill>/scripts/session.py new --task-file <任务.txt> --workspace <工作区>
python <skill>/scripts/bridge.py open --run <run> --out <run>/checks/window-001.json
python <skill>/scripts/bridge.py status --run <run> --out <run>/checks/status-001.json
```

`open` 返回 `window_opened:true` 且 `status` 返回 `composer:true` 后继续；附件操作见 [文本交接](text-handoff.md)。

```bash
python <skill>/scripts/session.py prepare --run <run> --file <本轮说明.txt>
python <skill>/scripts/bridge.py compose --run <run> --file <run>/messages/out-001.txt --expect-url <url> --out <run>/checks/compose-001.json
python <skill>/scripts/session.py submitting --run <run>
python <skill>/scripts/bridge.py send --run <run> --file <run>/messages/out-001.txt --expect-url <url> --out <run>/checks/send-001.json
python <skill>/scripts/session.py sent --run <run> --url <send返回的会话URL>
python <skill>/scripts/wait_reply.py --run <run> --file <run>/messages/out-001.txt --expect-url <会话URL> --out <run>/checks/wait-001.json --reply-out <run>/feedback/copied-001.txt
python <skill>/scripts/session.py reply --run <run> --file <run>/feedback/copied-001.txt --source dom
```

发送 `prepare` 生成的文件，它已追加本轮交接标记。`compose` 须返回 `draft_verified:true`，`send` 返回 `sent:true` 后才登记 `session.py sent`；只显示本地气泡时按下面的分支等待。结果不明先查原 job，不能重发。`send_pending` 不代表发送失败。

若 `send` 返回 `submitted:true, sent:false, confirmation:ui_only`，只证明页面出现本轮消息。保留 `send_pending`，用返回的会话 URL 运行同一个 `wait_reply.py`；取得完整匹配回复后再依次执行 `session.py sent` 和 `session.py reply`，不要重发。只有 `Thinking` 或停止按钮时，不能报告网页已收到材料或正在写正文。

`wait_reply.py` 自行等待完整回复，不需要模型轮询页面。终端每次最多等 55 秒；原进程未结束时保留它，不另启 watcher。正常等待不读状态文件、不截图；超时退出码 3 时用原参数加 `--resume`；退出码 2 时读 [恢复](recovery.md)。

登记成功后读取交接正文一次。完整原文另存为 `copied-001.txt.full.txt`，核对块外内容时再读。完成原任务并取得网页验收后，用 `session.py checkpoint --run <run> --phase complete --note-file <完成说明>` 记录完成。其他参数按需查看对应命令的 `--help`。

任务登记 `complete` 后自动提交窗口清理。仅关闭该任务标签页；有草稿、附件、生成中回复或最新消息不匹配时保留。聊天记录和登录不删除，至少保留一个浏览器窗口维持连接。

清理已有的完成任务（默认只预览，加 `--apply` 执行）：

```bash
python <skill>/scripts/cleanup_windows.py --workspace <工作区> --apply --wait 30
```

也可用 `--run <run>` 指定任务。需要查看已关闭的会话时，用 `bridge.py open --run <run> --restore --out <新检查文件>` 重新打开。
