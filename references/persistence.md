# 持久化、SQL与工作流状态

## 何时读取

当用户需要跨会话持续运行、多人候选人管理、大批量岗位、仪表盘、数据库或SQL存储时读取本文件。单次少量分析可以使用Markdown或JSON，不必强制建设数据库。

## 内置 SQLite 执行层

VerityApply for BOSS 已提供：

- `../scripts/schema.sql`：数据库结构、约束、索引和追加式事件触发器；
- `../scripts/job_tracker.py`：初始化、候选人建档、岗位写入、查重、授权、投递、定制招呼和统计命令。

使用Python标准库即可运行，不依赖第三方包。建议把数据库放在用户私有数据目录，而不是Skill目录或公开仓库。

初始化：

```bash
python3 scripts/job_tracker.py init --db /private/path/job-search.sqlite3
```

各写入命令接收JSON文件，也可以用 `--json -` 从标准输入接收，避免把敏感字段直接暴露在命令参数中：

```bash
python3 scripts/job_tracker.py upsert-candidate --db /private/path/job-search.sqlite3 --json -
python3 scripts/job_tracker.py upsert-resume --db /private/path/job-search.sqlite3 --json -
python3 scripts/job_tracker.py upsert-fact --db /private/path/job-search.sqlite3 --json -
python3 scripts/job_tracker.py upsert-job --db /private/path/job-search.sqlite3 --json -
python3 scripts/job_tracker.py record-evidence --db /private/path/job-search.sqlite3 --json -
python3 scripts/job_tracker.py check-duplicate --db /private/path/job-search.sqlite3 --job-key JOB_KEY --candidate-id CANDIDATE_ID
python3 scripts/job_tracker.py authorize --db /private/path/job-search.sqlite3 --json -
python3 scripts/job_tracker.py record-application --db /private/path/job-search.sqlite3 --json -
python3 scripts/job_tracker.py record-outreach --db /private/path/job-search.sqlite3 --json -
python3 scripts/job_tracker.py list --db /private/path/job-search.sqlite3 --candidate-id CANDIDATE_ID
python3 scripts/job_tracker.py stats --db /private/path/job-search.sqlite3 --candidate-id CANDIDATE_ID
```

`APPLIED_CONFIRMED`、`CONTACT_CONFIRMED` 和 `CUSTOM_OUTREACH_CONFIRMED` 都要求授权与验证证据。结果未知时写入未确认状态，不重复提交。提交授权必须包含非空的明确 `job_keys`，不能用空范围放行。

调用脚本时由Agent从已经确认的事实和当前状态构造JSON；不要要求普通用户手写数据库字段。`record-application` 会检查授权是否属于该候选人、是否仍在有效期、是否覆盖当前岗位和动作、批次上限是否已用完，以及简历/定制材料是否属于同一候选人并获准发送。`record-outreach` 还会检查定制内容引用的事实属于该候选人、已经确认且允许用于 `outreach`。

初始化脚本会把数据库目录限制为仅当前用户访问，并把数据库文件设为 `0600`。仍应优先使用磁盘加密或受保护的用户目录；不要同步到公开仓库。备份、导出、删除或迁移数据库前先向用户确认范围，并保持同等访问权限。

## 存储选择

- Markdown：适合用户阅读的候选队列、批次报告和决策摘要；
- JSON/YAML：适合单候选人的配置、状态恢复和工具间交换；
- SQLite/SQL数据库：适合多个候选人、长期活动、并发更新、查询统计和严格去重；
- 无论使用哪种存储，Skill中的状态机和授权规则仍是执行规范；SQL只保存事实和状态，不负责启动浏览器或替代外部动作授权。

## 推荐实体

### `candidates`

保存候选人主档、配置版本、创建时间和当前状态。敏感信息与普通求职偏好分开控制访问。

### `resume_versions`

保存文件标识、来源路径或对象ID、哈希、版本、批准状态、允许用途和生效时间。任何投递记录必须指向确定的简历版本。

### `candidate_facts`

保存事实ID、类型、原文、来源位置、验证状态、敏感等级和允许用途。材料生成只能引用允许使用且状态合适的事实。

### `campaigns`

保存目标岗位、地区、平台、硬门槛、偏好、评分配置、批次上限、总量上限和当前工作流状态。

### `jobs`

保存规范化公司、岗位、来源、链接、JD快照、在招状态、核验时间和去重键。

### `job_evidence`

按候选人隔离地连接岗位要求与候选人事实，保存 `DIRECT`、`TRANSFERABLE`、`MISSING`、`CONFLICT`、`UNVERIFIED`、迁移说明和置信度。

### `tailored_materials`

保存目标岗位、来源简历版本、材料类型、内容或文件标识、改动摘要、证据引用、审批状态和版本。

### `authorizations`

保存用户授权的动作、平台、岗位或批次范围、材料版本、数量上限、生效时间、过期时间、停止条件和撤销时间。

### `application_answers`

保存用户确认过的申请答案、适用范围、敏感等级、确认时间和有效期。未知问题不能自动写入。

### `applications`

保存岗位、候选人、活动、授权、材料版本、提交时间、结果状态、回执和最后核验时间。

### `outreach_messages`

保存逐岗定制招呼的版本、JD需求、事实证据ID、审批状态、发送状态和送达验证。定制招呼与申请状态分别统计。

### `workflow_events`

追加式记录状态变化、动作、执行者、原因、错误和时间。不要用覆盖历史的方式伪造连续状态。

## 关键约束

- `jobs` 对平台岗位ID或规范化岗位键设置唯一约束；
- 公司级去重启用时，在活动范围内增加公司唯一策略；
- `applications` 必须引用有效的候选人、岗位、授权和材料版本；
- `APPLIED_CONFIRMED` 必须存在验证证据或回执引用；
- 撤销授权不删除历史记录，但阻止后续外部动作；
- 使用事务同时更新申请结果、成功计数和事件日志，避免部分写入；
- 敏感字段按最小需要保存，不把验证码、密码、完整证件或平台会话密钥写入数据库；
- 数据库不可用或写入失败时停止外部提交，避免平台已操作但台账丢失。

## 工作流集成

```text
START
  → 读取 candidate + campaign + resume_versions
  → 写入本轮 workflow_event
  → 每个岗位完成核验后 upsert jobs
  → 写入 hard_gate、job_evidence 和评分
  → 材料生成后创建 tailored_materials 版本
  → 用户确认后创建 authorizations
  → 外部动作前在同一事务中检查授权和当前状态
  → 外部动作后写 applications 和 verification evidence
  → 对账后更新 campaign 状态并追加 workflow_event
```

内置实现使用SQLite，适合单机、单用户或轻量多候选人场景。需要PostgreSQL、MySQL、云端并发或多人权限时，以 `scripts/schema.sql` 的实体和约束为基线创建独立迁移；先确认数据库类型、部署位置、加密、访问控制和备份要求。

