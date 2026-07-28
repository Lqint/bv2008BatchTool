# bv2008 志愿时长批量录入桌面工具

这是一个面向 Windows 桌面使用的志愿北京/bv2008 批量录入工具。应用通过图形界面完成扫码登录、活动岗位查询、xlsx 导入、证明材料上传、批量入岗与时长录入，并把每行处理结果写回新的 xlsx 文件。

## 功能

- 扫码登录自动获取 TOKEN，也支持手动粘贴 TOKEN（调试）
- 输入活动 ID / 组织 ID 后获取岗位列表
- 在界面中下载导入模板和配套文档
- 上传 `.xlsx` 表格，逐行处理志愿者时长
- 上传 `.jpg/.png` 证明材料；不上传时自动使用 1x1 PNG 占位图
- 选择录入起始日期，按每天最多 10 小时自动拆分
- 输出 `*_result.xlsx`，新增或覆盖“录入结果”列
- 可用 PyInstaller 打包成 exe，给没有 Python 的电脑直接运行
- 每次网站请求完成后自动等待 1 秒，降低连续请求压力

## 表格格式

上传文件必须是 `.xlsx`，第一行表头必须包含：

```text
姓名、身份证号、岗位、时长、备注
```

说明：

- `姓名`、`岗位`、`时长` 必填。
- `时长` 只能填写整数或 `.5` 小数，例如 `1`、`1.5`、`0.5`。
- `身份证号` 单元格可留空；`备注` 要求必填，内容为空时程序会自动生成“日期+岗位+服务+时长h”。
- 有身份证号时，按姓名 + 身份证号查询志愿者。
- 没有身份证号时，仅按姓名查询；如果返回多名志愿者，会跳过该行并提示补充身份证号。
- `岗位` 必须匹配界面中获取到的岗位名称。
- 可点击界面中的“下载模板”生成 `模板.xlsx`。

## 完整流程

所有接口统一 POST `GATEWAY`（`/api-gateway/jpaas-jags-server/interface/gateway`），每次请求后自动等待 1 秒。公共参数：`app_id`、`interface_id`、`version`、`header`（含 `accessToken` + `accessSource:pc`）、`biz_content`、`charset:utf8`、`timestamp`、`origin:1`、`sign`（SM3 签名）。

### 一、登录

| 步骤 | interface_id | app_id | biz_content | 说明 |
|------|-------------|--------|-------------|------|
| 1 | `createCityCode` | zybjuser | `{}` | 获取登录二维码，返回 `codeId` + `codeContent`（二维码 URL） |
| 2 | `checkCodeStatus` | zybjuser | `{codeId}` | 轮询扫码状态，status=3 时返回 `accessToken` |

### 二、登录后初始化

| 步骤 | interface_id | app_id | biz_content | 说明 |
|------|-------------|--------|-------------|------|
| 3 | `zybjfrontcurrUserInfo` | zybjuser | `{}` | 获取用户组织列表，返回 `currOrgInfo`（含 `orgId`、`orgName`）+ `defaultOrgId` |
| 4 | `getInSm2Key` | zybjfront | `{}` | 获取平台 SM2 公钥 `pk`，后续姓名/身份证号均用此公钥加密传输（延迟到首次加密时调用） |

### 三、活动准备

| 步骤 | interface_id | app_id | biz_content | 说明 |
|------|-------------|--------|-------------|------|
| 5 | `findPostList` | zybjfront | `{activityId, orgId}` | 获取活动下的岗位列表，返回 `postName`、`iid`（作为 postId） |
| 6 | `findDetailsByIid` | zybjfront | `{iid: activityId}` | 获取活动详情，返回 `serverTime`（逗号分隔的活动日期列表，过滤未来日期后填入下拉框） |

### 四、批处理前准备

| 步骤 | interface_id | app_id | biz_content | 说明 |
|------|-------------|--------|-------------|------|
| 7 | `findRecruitVolunteerList` | zybjfront | `{pageNo, pageSize:50, state:"5", activityId, postId}` | 对每个岗位调用，分页拉取已入岗志愿者。返回 `uid`、`iid`、`name`（SM2 密文）、`nameSensitive`（脱敏姓名）。汇总为 `{uid → {post_id, post_name}}` 索引 |

### 五、逐行处理（每个志愿者）

#### 阶段一：查找可招募志愿者

| 步骤 | interface_id | app_id | biz_content | 说明 |
|------|-------------|--------|-------------|------|
| 8 | `activityUser-findOrgUserList` | zybjfront | `{pageNo:1, pageSize:10, name:SM2(name), certNo:SM2(certNo)(如有), activityId, postId, orgId}` | 姓名和身份证号（如有）用 SM2 公钥加密。服务端解密后匹配。查到多人（仅姓名且重名）→ 直接报错终止。查到 1 人返回 `uid` → 跳到步骤 12。查不到 → 步骤 9（有身份证号）或步骤 11（无身份证号） |

#### 阶段二：加入团体（需有身份证号）

| 步骤 | interface_id | app_id | biz_content | 说明 |
|------|-------------|--------|-------------|------|
| 9 | `addMember` | zybjfront | `{name:SM2(name), certNo:SM2(certNo)}` | 将志愿者加入团体。code=200 或 "已加入团体" 视为成功。失败 → 直接报错终止（未注册或信息错误）。成功 → 步骤 10 |
| 10 | `activityUser-findOrgUserList` | zybjfront | 同步骤 8 | 加入成功后重查。查到 → 步骤 12。查不到 ↓ |

#### 阶段三：枚举岗位查找已入岗记录（有身份证号，仅步骤 10 失败后进入）

| 步骤 | interface_id | app_id | biz_content | 说明 |
|------|-------------|--------|-------------|------|
| 10b | `findRecruitVolunteerList` | zybjfront | `{pageNo:1, pageSize:50, state:"5", activityId, postId, name:SM2(name), certNo:SM2(certNo)}` | 枚举所有岗位，传入加密的姓名+身份证号精准查找。查到 1 条后立即终止（一个志愿者只会出现在一个岗位中）：<br>• 在目标岗位 → 步骤 13（直接录时长）<br>• 在其他岗位 → 兼项报错<br>• 枚举完未找到 → 报错终止 |

#### 阶段四：查找团体成员（无身份证号，仅步骤 8 失败后进入）

| 步骤 | interface_id | app_id | biz_content | 说明 |
|------|-------------|--------|-------------|------|
| 11 | `findFormalMember` | zybjfront | `{name:明文!, loginName:"", pageNo:1, pageSize:50}` | 按明文姓名查团体成员（无需加密）。多人 → 报错终止。一人 → 取 `uid`，在步骤 7 的预取索引中查找：<br>• 在目标岗位中 → 步骤 13（直接录时长）<br>• 在其他岗位中 → 兼项报错<br>• 不在任何岗位 → 步骤 12 |

#### 阶段五：加入岗位 + 录入时长

| 步骤 | interface_id | app_id | biz_content | 说明 |
|------|-------------|--------|-------------|------|
| 12 | `activityUser-addList` | zybjfront | `{activityId, postId, orgId, uids:[uid]}` | 将志愿者加入岗位。"人员已经在此活动中"不视为错误 |
| 13 | `zybj_uploadFile` | zybjuser | `{file:{uid}, uploadType:"durationFile"}` + file | 上传证明材料。未选择文件时使用 1x1 PNG 占位图。返回 `newName` 作为 filePath |
| 14 | `activityTiming-batchAdd` | zybjfront | `{activityId, postId, orgId, uids:[uid], times:[{time, hour}], filePath, notes}` | 批量录入时长。`times` 按活动日期顺序分配，每天最多 10h。`notes` 为空时自动生成"日期+岗位+服务+时长h" |

### 六、结果输出

所有行处理完毕后，在原 xlsx 最后一列写入"录入结果"，保存为 `*_result.xlsx`。

## 本地运行

```bash
pip install -r requirements.txt
python bv_gui.py
```

TOKEN、活动 ID、组织 ID 都在界面中填写。接口地址 `GATEWAY` 固定写在 [bv_api.py](bv_api.py) 顶部。

## 打包

### Windows

```powershell
.\build_exe.ps1
```

生成 `dist\bv2008BatchTool.exe`，带 bv2008 网站图标。

### macOS

```bash
chmod +x build_mac.sh
./build_mac.sh
```

生成 `dist/bv2008BatchTool.app`，带 bv2008 网站图标。
首次打开时若提示"来自身份不明的开发者"，右键 → 打开即可。
跨架构打包需要在对应芯片的 Mac 上进行（Apple Silicon 或 Intel）。

## 文件结构

```text
├── bv_gui.py           # PySide6 桌面界面
├── bv_api.py           # bv2008 网关、登录、业务接口封装
├── bv_batch_runner.py  # xlsx 读取、批处理、结果写回
├── requirements.txt    # 运行与打包依赖
├── build_exe.ps1       # Windows 打包脚本
├── build_mac.sh        # macOS 打包脚本
├── logo.ico            # 应用图标
├── logo.png            # 应用图标
├── support_doc.png     # 配套文档
└── LICENSE
```
