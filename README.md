# bv2008 志愿者批量管理工具

逆向志愿北京（bv2008.cn）网关接口，实现志愿者批量导入与服务时数录入的自动化。

## ⚠️ 重要警示：录入时数前必须先 addList

**站点 bug**：`activityTiming-batchAdd` 不校验成员关系，直接写时数也能成功，但志愿者不会出现在岗位成员列表中，造成时数与名单不一致。

**必须按顺序**：`findOrgUserList` → `addList` → `upload` → `batchAdd`

`bv_batch_from_xls.py` 已在内部自动执行此顺序。

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

```bash
cp config.example.py config.py
```

编辑 `config.py`：

```python
TOKEN       = ""   # 从 bv_login.py 获取
ACTIVITY_ID = ""   # 活动 ID，见页面 URL
POST_ID     = ""   # 招募岗位 ID，见页面 URL
ORG_ID      = ""   # 组织 ID，见浏览器 devtools
```

### 3. 获取 accessToken

```bash
python3 bv_login.py
```

终端显示二维码，用支付宝/微信/百度 或 京通小程序扫码，确认后输出 token，粘贴进 `config.py`。

---

## 功能脚本

### 将志愿者加入团体成员池（addMember）

搜不到某人时（`findOrgUserList` 返回空），说明他未在本团体注册。先用此脚本将其加入：

```bash
python3 bv_add_org_member.py 施凯鑫 210221200601187519

# 批量（CSV，每行: 姓名,证件号）
python3 bv_add_org_member.py --file members.csv
```

- 成功：该人已在 bv2008 注册，现已加入团体，可被 `findOrgUserList` 搜到
- `50000 添加的人尚未注册为志愿者`：该人无 bv2008 账号，须先在平台自行注册

典型流程（新成员完整链路）：

```
addMember（加入团体）
  → findOrgUserList（拿 uid）
  → activityUser-addList（加入岗位）
  → activityTiming-batchAdd（录时数）
```

### 批量添加成员

```bash
# 从文件（一行一个姓名）
python3 bv_import.py names.txt

# 命令行
python3 bv_import.py 张三 李四 王五
```

### 录入单人时数

```bash
python3 bv_record_hours.py <uid> <YYYY-MM-DD> <小时数> [证明图路径]

# 示例（不带图：自动用 1x1 占位 PNG）
python3 bv_record_hours.py 90873434 2026-05-02 8
```

### 给已招募志愿者录入统一时数

不依赖姓名/XLS，直接从 `findRecruitVolunteerList` 拉岗位现有成员，批量赋予相同时数。成员已在岗，无需 addList。

```bash
# 预览名单（不提交）
python3 bv_hours_for_roster.py --hours 3 --start 2026-05-01 --dry-run

# 全员提交
python3 bv_hours_for_roster.py --hours 3 --start 2026-05-01

# 只提交指定 uid
python3 bv_hours_for_roster.py --hours 3 --start 2026-05-01 --filter-uid 90873434 234222082
```

### 从 Excel 批量录入时数

```bash
# 预览（不实际提交）
python3 bv_batch_from_xls.py roster.xls --dry-run

# 正式提交，从 2026-05-01 起，每天最多 8h，超出顺延
python3 bv_batch_from_xls.py roster.xls --start 2026-05-01 --max-hours 8
```

Excel 格式要求：第一行表头含 `学生姓名` 和 `认定时(次)数` 列。

### 撤销活动成员或时长记录

命令行工具 `bv_undo.py` 封装了两个删除接口。删除是远端永久操作，默认需要输入 `yes` 二次确认：

```bash
# 从活动删除成员：参数是岗位名单返回的成员关系 iid，不是 uid
python3 bv_undo.py member d9510734076a48c397fce56e2f00e570

# 删除时长记录：参数是时长记录 iid，并需要指定所属活动
python3 bv_undo.py timing --activity-id 1546164942829256704 0588534e3c64437ab83ef6806a18da50
```

可用空格或逗号一次传入多条 iid；自动化调用可增加 `--yes`。新版 Windows GUI 的侧栏也提供“撤销操作”页：成员可刷新名单后多选删除，时长记录可批量粘贴 iid 删除。

---

## 技术原理

### 网关

```
POST https://<host>/api-gateway/jpaas-jags-server/interface/gateway
Content-Type: multipart/form-data
```

form 字段：`app_id, interface_id, version, header, biz_content, charset, timestamp, origin, sign`

### 签名算法（SM3，无密钥）

```
sign = SM3("app_id={}&biz_content={}&charset={}&interface_id={}&origin={}&timestamp={}&version={}")
```

字段按字母序拼接，来源：前端 JS bundle `G8e` 函数。

### 敏感字段加密（SM2 C1C3C2）

```python
encrypted = "04" + SM2.encrypt(plaintext, pk, mode=1)
```

`pk` 由 `getInSm2Key` 接口每天轮换，`mode=1` = C1C3C2 字节序。来源：前端 `Y8e` 函数。

### 扫码登录（interface_id）

| 接口 | app_id | 说明 |
|---|---|---|
| `createCityCode` | zybjuser | 创建 QR，返回 codeId + codeContent URL |
| `checkCodeStatus` | zybjuser | 轮询：status=2 已扫 / status=3 返回 accessToken |

### 主要业务接口

| interface_id | app_id | 说明 |
|---|---|---|
| `getInSm2Key` | zybjfront | 取加密公钥 |
| `activityUser-findOrgUserList` | zybjfront | 按加密姓名查 uid |
| `activityUser-addList` | zybjfront | 批量加入岗位 |
| `activityUser-deleteList` | zybjfront | 按成员关系 iid 从活动删除成员 |
| `zybj_uploadFile` | **zybjuser** | 上传证明图，返回 newName |
| `activityTiming-batchAdd` | zybjfront | 录入时数 |
| `activityTiming-bathDelete` | zybjfront | 按记录 iid 删除指定活动的时长记录 |

---

## 注意事项

- **filePath 单次使用**：同一个 `zybj_uploadFile` 返回的 `newName` 只能用于一次 `batchAdd`，复用会报 `附件上传失败 (6203)`
- **accessToken 有效期约 1 小时**，过期重新 `bv_login.py`
- **inSm2Key 公钥每天轮换**，脚本每次运行自动拉取
- **重名**：`findOrgUserList` 多条结果时取第一条并打印 warning，必要时用 certNo 二次确认
- `config.py` 已在 `.gitignore` 中，不会上传 token

---

## 文件结构

```
├── config.example.py   # 配置模板（复制为 config.py 后填写）
├── config.py           # 本地配置（gitignored）
├── bv_login.py         # 扫码登录，获取 accessToken
├── bv_client.py        # 通用网关客户端
├── bv_import.py        # 批量添加成员
├── bv_add_org_member.py # 按姓名+证件号将志愿者加入团体成员池
├── bv_record_hours.py  # 单人时数录入
├── bv_undo.py          # 撤销活动成员 / 时长记录
├── bv_batch_from_xls.py# Excel 批量时数录入（按姓名搜索）
├── bv_hours_for_roster.py # 已招募成员批量时数（直接拉名单）
├── requirements.txt
└── .gitignore
```

---

## 获取组织 ID

如果已有 accessToken，但不知道 `ORG_ID`，可以先用 `bv_current_org.py` 查询当前账号可管理的组织：

```bash
python3 bv_current_org.py

# 或临时传入 token
python3 bv_current_org.py --token "<accessToken>"

# 输出完整 JSON
python3 bv_current_org.py --json
```

输出示例：

```text
user: *钦唐 uid=185037406
defaultOrgId: 223718004

org_id           parent_org_id    roles    org_name
223718004        3471936          1        中国人民大学商学院青协志愿者服务队
```

其中 `defaultOrgId` 和列表里的 `org_id` 都可以作为 `config.py` 里的 `ORG_ID`。

相关接口：

| interface_id | app_id | biz_content | 说明 |
|---|---|---|---|
| `zybjfrontcurrUserInfo` | zybjuser | `{}` | 获取当前用户信息、默认组织 ID、可管理组织列表 |

## 获取项目 ID

如果只知道组织 ID（`org_id` / `ORG_ID`），可以先用 `bv_list_projects.py` 拉取该组织下的项目列表：

```bash
python3 bv_list_projects.py

# 或临时指定组织 ID / token
python3 bv_list_projects.py --org-id 223718004 --token "<accessToken>"

# 输出 JSON，便于脚本继续处理
python3 bv_list_projects.py --org-id 223718004 --json
```

输出中的 `project_id` 对应接口返回字段 `iid`。

- `level=1`：一级项目
- `level=2`：二级项目，通常用于活动列表接口的 `projectId`

例如：

```text
level project_id                         parent_id                          date                    name
1     a45189b07db34dc28a55ba3ef5fd66aa   0                                  2026-06-01..2027-06-01  运动会志愿保障项目
2     23390729e65d4748b2954c7207b6aa7a   a45189b07db34dc28a55ba3ef5fd66aa   2026-06-01..2026-07-31    2026商学院春季运动会志愿保障
```

此时 `23390729e65d4748b2954c7207b6aa7a` 就是后续查询活动列表时使用的二级 `projectId`。

### 相关接口

| interface_id | app_id | biz_content | 说明 |
|---|---|---|---|
| `queryProjectList` | zybjfront | `{orgId}` | 获取组织下一级项目列表 |
| `queryProjectList` | zybjfront | `{orgId,parentId}` | 获取某个一级项目下的二级项目列表 |

`queryProjectList` 返回的项目 ID 字段名是 `iid`；脚本为了更直观，将它规范化输出为 `projectId`。
> 本工具由中国人民大学商学院青年志愿者协会开发，用于协会内部志愿服务项目管理与批量录入辅助。
## 新版图形界面（推荐）

推荐使用新的净室重构版本：

```bash
python3 bv_gui.py
```

新版入口位于 `bv_gui.py`，主体代码拆分在 `bv2008_gui/` 目录中：

- `api.py`：封装登录、组织、项目、活动、岗位、名单、录入接口
- `config_store.py`：本地配置读写
- `excel.py`：Excel 表头识别与推荐报表格式
- `widgets.py`：通用 Tkinter 控件
- `app.py`：登录、配置、名单、导入、撤销操作页面

### 姓名匹配与多账号选择

新版 GUI 支持四字姓名的 `**末两字` 和更长姓名的 `首字 + 中间星号 + 末两字` 脱敏形式，并兼容原有匹配形式。

批量导入会合并组织查询与当前岗位名单中的候选，按账号 ID（`uid`）去重。多个不同账号符合条件时，会弹出候选窗口，显示脱敏姓名、完整账号 ID、志愿者编号和岗位状态。核对后选择一行，再点击“确认使用此账号”；不会预选第一条。账号 ID 可从窗口下方的文本框复制。点击“跳过此人”、按 Esc 或关闭弹窗，会跳过该行的入岗和时长录入，继续处理其他行。

岗位中已有候选时，不再先尝试加入组织。已选账号 ID 会保留在日志及处理完成后的状态栏中。命令行为无交互入口，遇到多个候选时会在该行 JSON 的 `candidates` 中列出账号 ID，标记 `member: ambiguous` 并跳过该行；请在 GUI 中手动选择后处理。

本地验证：`python -m unittest discover -s tests -v`。测试使用模拟接口，弹窗测试会短暂创建 Tk 窗口，不会修改远端数据。

### Windows EXE / Agent CLI 双入口

同一个 `bv2008_gui.exe` 无参数启动窗口；增加 `cli` 后执行无交互命令，并只向标准输出写 JSON，适合 Agent 或脚本调用。源码运行时把 `bv2008_gui.exe` 换成 `python bv_gui.py` 即可。

```powershell
# 查看完整命令树；全局 --token 必须放在 cli 后、业务命令前
bv2008_gui.exe cli --help

# 扫码登录：先创建，再轮询并把成功 token 保存到 GUI 共用配置
bv2008_gui.exe cli login qr
bv2008_gui.exe cli login wait CODE_ID --save

# 查看/设置 GUI 共用配置（show 默认把 token 脱敏）
bv2008_gui.exe cli config show
bv2008_gui.exe cli config set --org-id ORG_ID --activity-id ACTIVITY_ID --post-id POST_ID

# 查询组织、项目、活动、岗位与名单
bv2008_gui.exe cli list orgs
bv2008_gui.exe cli list projects --org-id ORG_ID
bv2008_gui.exe cli list activities --project-id PROJECT_ID
bv2008_gui.exe cli list posts --activity-id ACTIVITY_ID
bv2008_gui.exe cli list roster

# Excel 全流程；正式写入必须显式 --confirm
bv2008_gui.exe cli import roster.xlsx --start 2026-09-01 --dry-run
bv2008_gui.exe cli import roster.xlsx --start 2026-09-01 --confirm

# 永久删除同样必须显式 --confirm
bv2008_gui.exe cli undo member MEMBER_RELATION_IID --confirm
bv2008_gui.exe cli undo timing TIMING_IID --activity-id ACTIVITY_ID --confirm
```

Token 也可通过 `BV2008_TOKEN` 环境变量临时提供，优先级为：全局 `--token`、环境变量、GUI 已保存登录态。CLI 退出码：`0` 成功、`1` 参数或普通错误、`2` 部分失败/等待超时、`3` 登录态错误。

Windows 打包脚本为 `build_windows.ps1`。它使用 Nuitka 的 `--windows-console-mode=hide`：从资源管理器打开时新控制台保持隐藏，从 PowerShell/CMD/Agent 启动时则复用调用方控制台、等待命令完成并返回退出码。

旧版 `bv_tui_windows.py` 保留作为回退，不再建议继续在旧文件上做大改。
