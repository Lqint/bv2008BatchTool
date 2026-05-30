# bv2008 志愿者批量导入

逆向志愿北京（test1.bv2008.cn）招募岗位的添加成员接口，按姓名批量加入指定 `activityId / postId / orgId`。

## ⚠️ 重要警示：录入时数前必须先 addList

**站点 bug**：`activityTiming-batchAdd` 不校验成员关系。即使 uid 从未通过 `activityUser-addList` 加入岗位，时数接口仍返回 `success:true` 并把记录写进志愿者档案。结果：

- 志愿者档案里有这段时数 ✓
- 但岗位成员列表（`findRecruitVolunteerList`）里查不到他 ✗
- 后台报表口径错位、导出/统计/审计可能漏数据

**已实测**：2026-05-29 给 postId `df285a48...` 的 4 人录入时数，3 人跳过 addList 直接 batchAdd 也成功了，但 web 端成员列表为空。

**正确流程**：每条录入前先 `activityUser-addList` 确保该 uid 已在岗，再 `zybj_uploadFile` + `activityTiming-batchAdd`。流程脚本 `bv_batch_from_xls.py` 后续需要补这一步。

## 流程总览

### A. 仅添加成员

```
姓名列表
  │
  ▼
[1] 取 inSm2Key 公钥 (getInSm2Key)
  │      ── 每天可能轮换，每次运行取一次缓存复用
  ▼
[2] 逐个 SM2 加密姓名 → 调 activityUser-findOrgUserList
  │      ── 返回 uid（明文）、nameSensitive（脱敏明文，用于人工核对）
  ▼
[3] 收集所有 uid → 一次 activityUser-addList 批量提交
```

### B. 添加成员 + 录入时数（完整链路，避开 bug）

```
xls/名单
  │
  ▼
[1] getInSm2Key 取公钥
  ▼
[2] findOrgUserList 搜姓名 → uid
  ▼
[3] activityUser-addList 把 uid 加入岗位       ◀── 别跳过！见顶部警示
  ▼
[4] 按规则切日：从 START_DATE 起，超 max_per_day 顺延
  ▼
[5] 循环每人：
      zybj_uploadFile 重新上传一张证明图（filePath 单次使用）
      activityTiming-batchAdd 写时数
```

## 逆向出的两条关键规则

### 1. 签名

```
sign = SM3(
  "app_id={app_id}&biz_content={biz_content}&charset={charset}"
  "&interface_id={interface_id}&origin={origin}&timestamp={timestamp}"
  "&version={version}"
)
```

- 字段按字母序固定拼接，无密钥
- 出处：`bootstrap-BIzyyNjw.js` 中 `G8e` 函数

### 2. 敏感字段 SM2 加密

```
encrypted = "04" + SM2.encrypt(plaintext, pk_in_hex, mode=C1C3C2)
```

- `pk` 来自 `getInSm2Key` 接口，每天轮换（response 含 `currDate=YYYYMMDD`）
- 模式 1 = C1C3C2 字节序（gmssl python `mode=1` 与 sm-crypto JS `cipherMode=1` 对齐）
- C1 不含 `04` 前缀，由本端拼上
- 出处：`bootstrap-BIzyyNjw.js` 中 `Y8e` 函数

适用字段：`name`、`certNo`、`mobile`、`loginName` 等。

## 网关

```
POST https://test1.bv2008.cn/api-gateway/jpaas-jags-server/interface/gateway
Content-Type: multipart/form-data
```

固定 form 字段：`app_id, interface_id, version, header, biz_content, charset, timestamp, origin, sign`。

`header` JSON：`{"accessToken":"...", "accessSource":"pc"}`
`biz_content` JSON：业务参数。

## 用到的 interface_id

| interface_id | app_id | 用途 | biz_content |
|---|---|---|---|
| `getInSm2Key` | zybjfront | 取入向公钥 | `{}` |
| `activityUser-findOrgUserList` | zybjfront | 按姓名查组织成员 | `{pageNo,pageSize,name(加密),activityId,postId,orgId}` |
| `activityUser-addList` | zybjfront | 批量加入招募岗 | `{activityId,postId,orgId,uids:[...]}` |
| `zybj_uploadFile` | **zybjuser** | 上传时数证明图片 | `{file:{uid:"vc-upload-..."},uploadType:"durationFile"}` + 另一个 form 字段 `file=<binary>` |
| `activityTiming-batchAdd` | zybjfront | 批量录入服务时数 | `{activityId,postId,orgId,notes,uids:[...],times:[{time:"YYYY-MM-DD",hour:N}],filePath:"<上传返回的 newName>"}` |

注意：`zybj_uploadFile` 的 `app_id` 不是 `zybjfront`，是 `zybjuser`，区别于其他接口。

## 文件

- `bv_login.py` — **扫码登录**，终端显示二维码，扫码后打印 accessToken
- `bv_client.py` — 通用客户端：`call()`、`make_sign()`、`sm2_encrypt()`、`get_in_sm2_pk()`、`unwrap()`
- `bv_import.py` — 批量导入志愿者入口脚本
- `bv_record_hours.py` — 单人时数录入脚本（自带上传证明图）
- `bv_batch_from_xls.py` — xls 批量录入时数（按规则切日，per-call 重新上传证明图）⚠️ **当前未在 batchAdd 前调 addList，使用时需先确保成员已入岗**
- `bv_hours_for_roster.py` — 给**已招募**成员批量录入统一时数（从 `findRecruitVolunteerList` 拉名单，无需搜姓名）
- `requirements.txt` — gmssl + requests
- `names.example.txt` — 姓名清单样例（一行一个）
- `api.md` — 原始抓包记录

## 安装

```bash
pip install -r requirements.txt
# 或受限环境：pip install --break-system-packages -r requirements.txt
```

## 获取 accessToken（扫码登录）

```bash
python3 bv_login.py
```

终端打印 ASCII 二维码，用支付宝/微信/百度 或 京通小程序扫码，确认后输出：

```
[2/2] 登录成功！accessToken:

  eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...

将上方 token 粘贴到各脚本顶部的 TOKEN 常量。
```

将输出 token 替换 `bv_import.py`、`bv_record_hours.py`、`bv_batch_from_xls.py` 顶部的 `TOKEN = "..."` 即可。

**登录原理**（逆向自 `qrLoginModel-Cbim3Liu.js`）：

1. `createCityCode` (app_id=`zybjuser`) → `{codeId, codeData.codeContent}`
2. 将 `codeContent` URL 生成二维码展示
3. 每 3 秒轮询 `checkCodeStatus` 传 `{codeId}`：
   - status=`2` → 已扫码等待确认
   - status=`3` → 确认完成，返回 `data.accessToken`

## 配置

编辑 `bv_import.py` 顶部常量：

```python
TOKEN       = "..."   # JWT accessToken，从浏览器登录后抓
ACTIVITY_ID = "..."   # 活动 ID
POST_ID     = "..."   # 招募岗位 ID
ORG_ID      = "..."   # 组织 ID
```

三个 ID 在招募岗位详情页的 URL query 或 `currOrgInfoActive.orgId` 中可见。

## 运行

### 批量导入志愿者

```bash
# 从文件批量
python3 bv_import.py names.example.txt

# 命令行直接传姓名
python3 bv_import.py 毕海欣 张三 李四
```

输出样例：

```
[1/3] fetch inSm2Key public key...
      pk=04a6a08214563ac414e6... (len=130)
[2/3] search 1 name(s) → uid
      ✓ 毕海欣 → uid=90873434 (*海欣, userNumber=110108104264802)
[3/3] addList batch of 1 uid(s)
      ok. added 1, missing 0.
```

### 录入服务时数

```bash
# 不带证明图：自动用一张 1x1 占位 PNG
python3 bv_record_hours.py <uid> <YYYY-MM-DD> <小时数>

# 带证明图
python3 bv_record_hours.py 90873434 2026-05-02 8 ./proof.png
```

流程：
1. `zybj_uploadFile` 上传图片 → 拿 `newName`（如 `d838231e47554b89bc71b18576632170.png`）
2. `activityTiming-batchAdd` 用 `newName` 作 `filePath`，提交 `uids + times`

`uid` 从前述 `findOrgUserList` 拿到，也可在 `bv_import.py` 输出里看到。

### 给已招募志愿者录入统一时数

不依赖姓名/XLS，直接从 `findRecruitVolunteerList` 拉岗位现有成员，批量赋予相同时数。

```bash
# 预览名单（不提交）
python3 bv_hours_for_roster.py --hours 3 --start 2026-05-01 --dry-run

# 全员提交
python3 bv_hours_for_roster.py --hours 3 --start 2026-05-01

# 只提交指定 uid（空格分隔）
python3 bv_hours_for_roster.py --hours 3 --start 2026-05-01 --filter-uid 90873434 234222082
```

适用场景：活动结束后给所有已到场成员统一记录时数。成员已在岗，无需 addList。

## 注意事项

- ⚠️ **batchAdd 必须前置 addList**：见顶部警示。`activityTiming-batchAdd` 不校验成员关系，跳过 addList 会造成时数有记录但岗位成员列表无该人的数据不一致。
- ⚠️ **filePath 单次使用**：同一个 `zybj_uploadFile` 返回的 `newName` 只能在一次 `batchAdd` 里用，第二次复用会得 `附件上传失败 (6203)`。每条录入前都要重新上传一张。
- **accessToken 有效期**：JWT 默认 1 小时，过期换 token 重跑
- **公钥每天轮换**：脚本每次运行重新拉取，无需手工更新
- **重名风险**：同岗位 `findOrgUserList` 返回多个匹配时取第一条并 warn，必要时改用 `certNo` 二次过滤
- **批量上限**：`addList` 一次接受多个 uid，未实测上限；保险按 50/批切片
- **时数顺延规则**：`bv_batch_from_xls.py` 默认 `--start 2026-05-01 --max-hours 8`，单人总时数若 >8h 自动滚到次日及之后
- **法律边界**：仅在已授权场景使用（自己负责的组织、合规的志愿者管理流程）
