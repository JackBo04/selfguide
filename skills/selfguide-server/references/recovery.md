# 收发恢复

先读具体结构化错误与 `session.py status --run <run> --brief`，按路径读取最新交接或检查记录。只有需要追溯时才读取完整状态（省略 `--brief`），不要把全部轮次反复读进上下文。

- 项目入口跳到首页：新版页面通过项目选择器的项目 ID 确认归属；检查结果的 `url` 是用于恢复的项目地址，`page_url` 是浏览器实际地址。不要把普通首页当作已绑定项目。
- 页面 `waiting/page_loading` 或正在生成：保留原等待进程，不截图。watcher 超时加 `--resume`；登录等阻塞处理完后也用它继续。
- 桥接可达但打开命令一直未领取：先查 `browserctl.py status`。浏览器退出时，使用原配置执行 `browserctl.py start`，保留登录目录；确认原 job 已过期且未领取后，才能重新打开原任务。只有网页实际要求登录或验证时才请用户接手。
- `website_rejected`／网页 `Unknown error`：停止重复发送，保留原正文与 job。`local-chatgpt%3A…` 是临时地址，不是成功创建的服务器会话。若诊断确认 HTTP 403 且 `cf-mitigated: challenge`，需要网站验证恢复；主页有输入框并不代表请求已恢复。
- 不确定是否发送／上传成功：查 `bridge.py job <ID>`。不要重复提交同一操作。仍未确定时保持原任务状态；需要暂停可用 `session.py checkpoint --phase paused --run <run> --note-file <说明>`，恢复用 `session.py resume --run <run>`。
- 普通小型状态不足时，可用 `bridge.py snapshot` 获取文本诊断；它包含最近消息与草稿，只在此时使用。
- `page_render_failed` 表示网页显示重试页且没有输入框。核对原 job，确认本窗口没有未确认发送、草稿或上传后，只刷新这个窗口；不要借用别的任务窗口。消息关联校验失败时，使用本任务当前 `prepare` 产物，不改任务 ID 来绕过检查。
- 文本仍无法定位问题时，先 `bridge.py focus --run <run> --expect-url <任务URL> --out <检查文件>` 切到该任务窗口，再用 `desktop.py screenshot --reason <具体异常> --out <诊断.png>`。本地浏览器需本地可见工具或用户查看，服务器截图不能代表本地画面。`screenshot_recommended` 只是提示，不自动触发截图。解决后返回 DOM 通道。

登录、人机验证由用户处理，不改变指纹或绕过验证。DOM 结构可能变化，联调成功不代表实站可靠或避免风控。

窗口关闭或重启后绑定未确认时，用 `bridge.py open --run <run> --restore --out <检查文件>` 恢复已登记的会话地址。首次发送尚未确认时先处理原 job；不借用其他任务窗口。旧任务未使用 `--run` 的待收回复继续沿用原 watcher 参数；该轮完成后才能用 `open --run` 为原会话创建专用窗口。

继续已完成任务：先 `session.py resume --run <run>`，再 `bridge.py open --run <run> --restore --out <新检查文件>`；沿用原会话 URL、任务 ID 和轮次。若关闭 job 尚未结束，先等原 job 完成再恢复，不另建任务。
