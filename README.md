# 产后照护计划协同系统

护理、营养、康复、心理支持四类专业人员与服务主管、产妇共同维护当日照护计划。
基于 **Django REST Framework + SQLite**，解决"各专业各改一处、安排互相冲突"的问题。

## 核心规则

| 需求 | 实现 |
| --- | --- |
| 四类人员提交评估/建议/禁忌/执行反馈 | `ProfessionalOpinion`（只追加），`POST /api/plans/{id}/opinions/` |
| 每个计划版本绑定当时有效的专业意见 | `PlanVersion` + `VersionItem`（项目快照）+ `VersionOpinion`（意见冻结） |
| 新不适/新禁忌立即暂停 | 同条线 → `PAUSED`；任何专业人员都可立即暂停 |
| 跨专业意见矛盾 | 自动进入 `IN_CONFLICT` 会商状态，`ConsultationIssue.missing_decision` 明确"尚缺的决定" |
| 恢复必须有权限专业人员重新确认 | 仅项目所属条线专业人员可恢复，且会商未决一律拒绝；恢复写入 `ResumeEvent` + CONFIRMATION 意见 |
| 产妇拒绝 | 仅产妇本人可提交，`Refusal` 永久保留拒绝范围与时间；工作人员不能代取消、不能暂停/恢复已拒绝项目 |
| 完成记录只追加 | `CompletionRecord` 只增不改，后续暂停/调整不影响历史记录 |
| 产妇端视图 | `GET /api/mother/today/`：已确认安排、暂停原因、可选择事项、本人拒绝 |
| 主管追溯 | `GET /api/plans/{id}/changes/`（每次变更依据）、`/api/supervisor/issues/open/`（未解决冲突） |
| 并发变更防护 | 计划带 `revision` 乐观锁，所有写操作必须携带 `expected_revision`；过期或撞锁返回 **409** |

## 角色

`MOTHER` 产妇 · `NURSE` 护理 · `NUTRITIONIST` 营养 · `REHABILITATOR` 康复
· `PSYCHOLOGIST` 心理支持 · `SUPERVISOR` 服务主管

- 任何专业人员都能**立即暂停**任何项目（跨条线暂停自动转会商）；
- 项目的**确认/恢复**只有其所属条线人员能做；
- **会商决定**只有服务主管能做；决定后项目仍处暂停，必须条线人员重新确认才恢复；
- 产妇只能看自己的计划、拒绝项目、选择可选项。

## 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo        # 创建 6 个演示账号（密码均 123456）与当天计划
python manage.py runserver
```

登录获取令牌：

```bash
curl -s -X POST http://127.0.0.1:8000/api/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"nurse","password":"123456"}'
# 后续请求头：Authorization: Token <token>
```

## 典型流程

```bash
# 1. 康复师因产妇伤口不适立即暂停康复项目（立即生效）
POST /api/plans/1/items/3/pause/
  {"reason": "产妇反映伤口疼痛", "expected_revision": 10}

# 2. 营养师提交同条线禁忌 → 餐食立即暂停
POST /api/plans/1/opinions/
  {"opinion_type":"CONTRAINDICATION","content":"停用当归活血食材",
   "item":2,"expected_revision":11}

# 3. 护理师对心理项目提跨条线禁忌 → 自动进入会商，登记尚缺决定
#    主管看待决冲突：GET /api/supervisor/issues/open/
#    主管会商决定：POST /api/issues/1/resolve/ {"decision":"PROCEED",...}
#    会商后项目仍暂停 → 心理师重新确认才恢复：
POST /api/plans/1/items/4/resume/
  {"comment":"已改卧位方案","expected_revision":14}

# 4. 产妇拒绝（范围与时间永久保留，他人不可取消）
POST /api/plans/1/items/3/refuse/
  {"scope":"仅今日","reason":"今天太累","expected_revision":15}

# 5. 发布不可变版本（快照项目 + 绑定当时全部意见）
POST /api/plans/1/publish/ {"note":"下午版本","expected_revision":16}
```

所有写接口都要带 `expected_revision`（取自 `GET /api/plans/{id}/` 的 `revision`）；
他人先行修改后，旧 revision 提交返回 `409 revision_conflict`，刷新后重试即可。

## 主要接口

| 方法与路径 | 说明 |
| --- | --- |
| `POST /api/auth/login/` | 获取 Token |
| `GET/POST /api/plans/` | 计划列表/建立（产妇只见本人） |
| `GET/POST /api/plans/{id}/items/` | 项目列表/新增 |
| `GET/POST /api/plans/{id}/opinions/` | 专业意见列表/提交 |
| `POST …/items/{iid}/pause/` `/resume/` `/confirm/` `/complete/` `/refuse/` `/select/` | 项目动作 |
| `POST /api/plans/{id}/publish/` | 发布版本 |
| `GET /api/plans/{id}/versions/` | 版本与绑定意见（不可变历史） |
| `GET /api/plans/{id}/changes/` | 变更审计 |
| `GET /api/plans/{id}/issues/` | 会商议题 |
| `POST /api/issues/{id}/resolve/` | 主管会商决定 |
| `GET /api/supervisor/issues/open/` | 主管视角的未解决冲突 |
| `GET /api/mother/today/` | 产妇端当天视图 |

## 并发设计说明

- `Plan.revision` 为乐观锁；服务层每个写事务以
  `UPDATE … WHERE id=? AND revision=?` 条件更新抢占修订号，成功才继续，
  否则整体回滚并返回 409。
- SQLite 配置 `BEGIN IMMEDIATE` + WAL + `busy_timeout`，写事务排队而非互相覆盖；
  锁等待超时同样翻译为 409。
- 状态校验放在抢占修订号**之后**，保证基于过期页面的提交一定得到版本冲突，
  不会恰好撞上他人刚写入的新状态而产生误判。

## 测试

```bash
python manage.py test care
```

- `tests/test_versions.py`：版本快照不可变、意见按发布时点绑定
- `tests/test_roles.py`：六类角色与条线权限
- `tests/test_workflow.py`：暂停/恢复确认、会商、禁忌、产妇拒绝、完成追加、全天场景
- `tests/test_concurrency.py`：过期 revision 409、多线程真实并发恰有一方成功
