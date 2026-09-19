# 产后照护计划协同

本项目供护理、营养、康复和心理支持人员共同维护产后照护计划，记录专业意见、禁忌、暂停与恢复依据。应用采用 Python，保留计划版本和产妇选择。

技术栈：Django 5 + Django REST Framework + SQLite。

## 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo        # 演示数据：各角色用户 + 当日计划
python manage.py runserver
```

演示用户（密码均为 `demo1234`）：`mother1`（产妇）、`nurse1`（护理）、
`nutrition1`（营养）、`rehab1`（康复）、`psych1`（心理支持）、`boss`（服务主管）。
获取令牌：`POST /api/auth-token/`（`{"username": "...", "password": "..."}`），
之后请求头带 `Authorization: Token <token>`。

运行测试：`python manage.py test care`（含计划版本、角色权限与并发变更测试）。

## 核心规则

- **计划版本**：每次变更（加项目、提意见、暂停/恢复/完成/拒绝、会商解决）都要求
  请求体携带 `base_revision`，服务端以 compare-and-swap 方式校验；成功后生成新的
  `PlanVersion`，绑定当时有效的专业意见并快照全部项目状态。版本号过期返回
  `409` 及当前 `current_revision`。
- **专业意见**：护理/营养/康复/心理支持人员提交评估、建议、禁忌、执行反馈；
  意见只追加，新意见可取代（supersede）本专业的旧意见。禁忌默认立即暂停关联项目，
  评估也可通过 `pause_item_ids` 显式暂停（如产妇临时反馈伤口不适）。
- **恢复确认**：已暂停项目只能由该项目所属专业的人员或服务主管确认恢复，恢复时
  生成一条确认评估意见作为依据；存在有效禁忌或未解决会商时禁止恢复。
- **产妇拒绝**：仅产妇本人可拒绝（记录范围：仅本次/今日内/该项目全部，及时间）；
  撤销也只能由产妇本人发起，其他人员（含服务主管）代为取消返回 `403`；记录保留不删除。
- **完成记录**：只追加——API 只读，模型层禁止修改与删除，不因后来调整而消失。
- **会商**：同一项目上跨专业的"建议"与"禁忌"并存时自动进入会商状态，相关项目
  保持暂停，`pending_decisions` 明确尚缺的决定及需参与的专业；服务主管对每项
  待决事项给出结论后计划恢复执行中。

## API 一览

| 端点 | 说明 |
| --- | --- |
| `POST /api/plans/` | 创建当日计划（工作人员） |
| `POST /api/plans/{id}/add_item/` | 添加本专业项目 |
| `POST /api/plans/{id}/submit_opinion/` | 提交专业意见（可关联项目、取代旧意见、暂停项目） |
| `GET /api/plans/{id}/versions/` | 版本历史（每版绑定当时有效意见） |
| `GET /api/plans/{id}/today/` | 产妇端：已确认安排、暂停原因、可选择事项 |
| `GET /api/plans/{id}/audit/` | 服务主管：变更依据（事件+版本）、完成与拒绝记录、未解决冲突 |
| `POST /api/items/{id}/pause/` | 立即暂停（工作人员或产妇本人，须填原因） |
| `POST /api/items/{id}/resume/` | 恢复（所属专业或主管确认，可顺带撤销禁忌） |
| `POST /api/items/{id}/complete/` | 追加完成记录 |
| `POST /api/items/{id}/refuse/` | 产妇拒绝（记录范围与时间） |
| `POST /api/refusals/{id}/revoke/` | 产妇本人撤销拒绝 |
| `GET /api/consultations/` · `POST /api/consultations/{id}/resolve/` | 会商列表与解决 |
| `GET /api/completions/` | 完成记录（只读） |

## 目录结构

```
config/            Django 项目配置
care/
  models.py        用户角色、计划、项目、意见、版本、完成记录、拒绝、会商
  services.py      业务规则：乐观锁变更、自动暂停、冲突检测、恢复确认
  permissions.py   角色权限（产妇/四类专业/服务主管）
  views.py         DRF 视图（DomainError→400，RevisionConflict→409）
  tests/           工作流、版本绑定、角色权限、并发变更测试
```
