# 岗位来源、平台接入与有界恢复

## 何时读取

在需要自动搜索招聘平台、打开岗位、提交申请、建立招聘会话，或平台连接失败时读取本文件。只分析用户提供的离线JD时不需要读取。

## 岗位来源

每个岗位记录来源及核验状态。支持：

- `USER_PROVIDED`：用户提供链接、JD、截图或文件；
- `AUTO_PLATFORM_SEARCH`：在招聘平台主动检索；
- `TARGET_COMPANY_SEARCH`：按目标公司官网或招聘页检索；
- `IMPORTED_ALERT`：邮件、订阅、表格或其他合法来源导入。

用户提供的岗位和自动搜索不是互斥模式，可以合并、规范化和去重。任何来源都必须经过当前在招核验；导入来源不自动获得更高可信度。

## 平台接入方式

在 `PLATFORM_ACCESS_GATE` 选择当前平台实际支持的一条主线路：

1. `OFFICIAL_API_OR_CONNECTOR`：优先使用可用的官方API或已授权连接器；
2. `EXISTING_AUTHENTICATED_BROWSER`：连接用户已登录且明确指定的浏览器或标签；
3. `USER_TERMINAL_LAUNCHED_BROWSER`：由用户在正常桌面会话中从终端启动专用浏览器，Agent只连接和检查；
4. `MANUAL_HANDOFF`：自动化无法安全完成时，生成明确步骤和材料，由用户手动操作。

不要同时控制多条线路来猜测哪个页面正确。线路改变后重新进入 `START`，重建当前岗位和授权状态。

## 接入配置

每个平台适配器至少声明：

```text
platform_name
access_method
authentication_owner
launch_owner              # user | agent | connector
launch_instruction
connection_endpoint
required_login_state
supported_actions
unsupported_actions
automatic_side_effects
verification_method
max_connection_attempts
stop_conditions
```

终端启动命令属于平台适配配置，不属于候选人偏好。只使用已经安装、用户认可且目标明确的启动入口；不要凭空生成会修改系统、复制登录数据或绕过平台限制的命令。

## 平台接入闸门

自动检索或投递前检查：

- 目标平台和账号属于本次授权范围；
- 登录态有效，页面或接口可读取；
- 当前公司、岗位和页面可以唯一识别；
- 支持的动作与本轮模式一致；
- 自动问候、自动附件、自动回答等副作用已知；
- 结果能够通过申请记录、回执或会话验证。

未通过时不进入搜索或提交节点。

## 终端启动线路

当平台必须通过专用浏览器接入时：

```text
USER_TERMINAL_LAUNCH_GATE
  → 向用户显示已配置的启动入口和预期现象
  → 等待用户确认本轮已经启动
  → 检查连接端点
  → 检查登录态
  → 检查岗位页可读性
  → 通过后进入搜索或投递
```

Agent不得把“上一轮曾经成功”当成本轮已启动证据，也不得在用户负责启动的配置中擅自启动或重启桌面浏览器。

## 有界恢复

连接故障使用一个全链路共享计数器。默认最多3次，可由平台配置降低；不得因切换工具、标签或子步骤而重新计数。

```text
PLATFORM_ACCESS_GATE
  ├─ 成功 → CONTINUE
  └─ 失败 → CONNECTION_RECOVERY_GATE
               ├─ 未达到上限且存在安全恢复动作 → 恢复后返回 START
               └─ 达到上限或无安全恢复动作 → PLATFORM_BLOCKED
```

故障类型至少区分：

- `ENDPOINT_UNREACHABLE`；
- `LOGIN_REQUIRED`；
- `WRONG_ACCOUNT_OR_INSTANCE`；
- `PAGE_UNREADABLE`；
- `BROWSER_OR_CONNECTOR_CRASHED`；
- `PLATFORM_RATE_LIMITED`；
- `CAPTCHA_OR_VERIFICATION_REQUIRED`。

三次上限只适用于连接恢复，不允许用来连续重复提交同一申请。提交结果未知时标记 `SUBMITTED_UNCONFIRMED`，先核对平台记录，禁止盲目再次点击。

## 降级与兜底

- 自动搜索不可用：转为用户提供岗位、目标公司清单或人工导入；
- 自动读取不可用：请求用户提供JD正文或截图；
- 自动提交不可用：输出已批准材料和手动操作步骤；
- 无法验证结果：标记未确认并让用户检查，不冒充成功；
- 出现验证码、身份验证、风控或平台限制：停止自动化并交还用户，不尝试绕过。

兜底路径改变执行方式，不降低岗位真实性、证据、授权和结果验证标准。

