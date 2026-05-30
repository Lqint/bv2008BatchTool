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
# 从文件（每行: 姓名,身份证号；也兼容一行一个姓名）
python3 bv_import.py names.example.txt

# 单人：姓名 + 身份证号共同匹配
python3 bv_import.py --cert-no 110101199001011234 张三

# 命令行只传姓名时仍可用，但重名时不如证件号精确
python3 bv_import.py 张三 李四 王五
```

### 查看活动岗位

通过活动 ID 和组织 ID 查询该活动下的岗位名称和岗位 ID：

```bash
# 使用 config.py 里的 ACTIVITY_ID / ORG_ID
python3 bv_find_posts.py

# 或在命令行指定
python3 bv_find_posts.py --activity-id 1510325811016630272 --org-id <orgId>
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

Excel 格式要求：第一行表头含 `学生姓名` 和 `认定时(次)数` 列。若同时含 `身份证号`、`身份证号码`、`证件号` 或 `证件号码` 列，脚本会用姓名 + 证件号共同查询 uid。

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
| `addMember` | zybjfront | 按加密姓名和证件号加入团体成员池 |
| `findPostList` | zybjfront | 按 `activityId` 和 `orgId` 查询岗位名称与岗位 ID |
| `activityUser-findOrgUserList` | zybjfront | 按加密姓名查 uid；有证件号时同时传加密 `certNo` |
| `activityUser-addList` | zybjfront | 批量加入岗位 |
| `zybj_uploadFile` | **zybjuser** | 上传证明图，返回 newName |
| `activityTiming-batchAdd` | zybjfront | 录入时数 |

---

## 注意事项

- **filePath 单次使用**：同一个 `zybj_uploadFile` 返回的 `newName` 只能用于一次 `batchAdd`，复用会报 `附件上传失败 (6203)`
- **accessToken 有效期约 1 小时**，过期重新 `bv_login.py`
- **inSm2Key 公钥每天轮换**，脚本每次运行自动拉取
- **重名**：优先提供身份证号/证件号，让 `findOrgUserList` 用姓名 + `certNo` 共同匹配；仍返回多条时脚本会取第一条并打印 warning
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
├── bv_find_posts.py    # 按活动 ID 查询岗位名称和岗位 ID
├── bv_record_hours.py  # 单人时数录入
├── bv_batch_from_xls.py# Excel 批量时数录入（按姓名搜索）
├── bv_hours_for_roster.py # 已招募成员批量时数（直接拉名单）
├── names.example.txt   # 姓名名单示例
├── api.md              # 接口抓包/逆向记录模板
├── requirements.txt
└── .gitignore
```
