# 原生 CLI 事件异常与共享连接隔离

Status: implemented

## 故障

Codex 0.164 的正常命令开始事件会发送 `aggregatedOutput: null`，随后以字符串增量通知输出。CLI 适配器此前直接把这个空值与增量相加，触发 `TypeError`。原生读取循环把事件处理异常与 EOF、协议帧错误放在同一个异常边界内，最终终止共享代理并清除所有已接入线程，尚未发送的队列也因此停止派发。

隔离夹具使用真实 OS 管道、WebSocket 握手和实际 `CliSessions` 接入两个合成线程；发送开始事件、连续输出增量及后续 RPC。修复前，正常的空输出使后续请求变成 `AppServerUnknownResult`，两个线程一起失去连接。事件和审批回调抛错也会复现同一问题。

## 修改

仅把命令输出的 `None` 规范化为空字符串，保留已有输出并继续累加。其他不符合协议的字段仍进入错误处理，不把它们伪装成有效状态。

`CodexAppServer` 在调用事件、原生请求回调时单独隔离异常，继续处理后续帧和 RPC。CLI 适配器按发生错误的原代理身份撤销相关线程的控制状态，保留共享代理和其他线程。旧代理的错误不能撤销新连接；无法识别线程归属的回调不修改已核验线程。

接入中的线程出现回调错误时，取消该次接入标记和请求缓存；安装前再次检查原代理及接入标记，迟到的成功响应不能重新安装已经失效的控制状态。清理在客户端锁内完成，通知上层时释放该锁。

错误日志只记录固定回调类别和异常类名，不记录协议内容、异常文本或堆栈。错误清理本身失败也不终止读取循环。

实际 EOF、关闭帧、无效 WebSocket 帧和其他读取失败继续沿用原有连接清理及未知结果规则。普通响应超时仍只结束对应请求。错误处理不发送审批响应、不启动或恢复线程、不重放未知写入。本次范围没有加入 CLI 自动重接或队列自动恢复。

## 验证

直接回归覆盖空输出连续累加、两个线程和共享读取循环保持可用、事件或审批回调错误后的后续 RPC，以及旧代理错误不影响新接入。安全对照检查真实 EOF、坏帧及普通请求超时，保留未知写入不重放的约束。

新增 12 个回归用例，强化 1 个响应超时用例。App Server 与 CLI 两组共 89 项通过；CLI 共用调用链 45 项、原生标题生成 21 项、自动标题 17 项通过。最终完整后端源码回归共 3,593 项，3,545 项通过、48 项按源码范围或平台条件跳过。模块边界检查通过，原有 22 条精确例外没有变化。

自动标题及 CLI 共用调用链继续使用原生协议，没有改写其模型、provider、认证或工作目录。验证使用合成数据；本轮不运行真实客户端任务、原生编译、安装或发布。

测试入口：

```sh
python -B scripts/test-backend.py --source-only -p test_codex_app_server.py
python -B scripts/test-backend.py --source-only -p test_codex_cli_session.py
python -B scripts/test-backend.py --source-only -p test_cli_bridge_integration.py
python -B scripts/test-backend.py --source-only -p test_codex_title_generation.py
python -B scripts/test-backend.py --source-only -p test_auto_titles.py
python -B scripts/test-backend.py --source-only
```

完整源码回归使用已有项目环境；`--source-only` 跳过原生编译及执行夹具，不代表真实手机、SSH 或桌面应用已完成联调。
