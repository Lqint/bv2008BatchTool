# bv2008 志愿时长批量录入桌面工具

这是一个面向 Windows 桌面使用的志愿北京/bv2008 批量录入工具。应用通过图形界面完成扫码登录、活动岗位查询、xlsx 导入、证明材料上传、批量入岗与时长录入，并把每行处理结果写回新的 xlsx 文件。

## 功能

- 扫码登录自动获取 TOKEN，也支持手动粘贴 TOKEN（调试）
- 输入活动 ID / 组织 ID 后获取岗位列表
- 上传 `.xlsx` 表格，逐行处理志愿者时长
- 上传 `.jpg/.png` 证明材料；不上传时自动使用 1x1 PNG 占位图
- 选择录入起始日期，按每天最多 10 小时自动拆分
- 输出 `*_result.xlsx`，新增或覆盖“录入结果”列
- 可用 PyInstaller 打包成 exe，给没有 Python 的电脑直接运行
- 每次网站请求完成后自动等待 3 秒，降低连续请求压力

## 表格格式

上传文件必须是 `.xlsx`，第一行表头必须包含：

```text
姓名、身份证号（选填）、岗位、时长
```

说明：

- `姓名`、`岗位`、`时长` 必填。
- `身份证号` 单元格可留空。
- 有身份证号时，按姓名 + 身份证号查询志愿者。
- 没有身份证号时，仅按姓名查询；如果返回多名志愿者，会跳过该行并提示补充身份证号。
- `岗位` 必须匹配界面中获取到的岗位名称。

## 处理流程

每一行按以下顺序执行：

```text
findOrgUserList（姓名+身份证号，或仅姓名）
  → 找不到且有身份证号：addMember 加入团体，再重新查询
  → addMember 返回“该用户已加入团体”时视为成功
  → 找不到且无身份证号：跳过并写入原因
  → 查询到多人：跳过并提示补充身份证号
  → activityUser-addList 加入岗位
  → 检查起始日期到今天是否足够容纳时长（每天最多 10 小时）
  → zybj_uploadFile 上传证明图
  → activityTiming-batchAdd 录入时数
```

注意：`activityTiming-batchAdd` 不校验成员是否已入岗，所以本工具始终先执行 `activityUser-addList`，避免时长记录和岗位名单不一致。

## 本地运行

```bash
pip install -r requirements.txt
python bv_gui.py
```

TOKEN、活动 ID、组织 ID 都在界面中填写。接口地址 `GATEWAY` 固定写在 [bv_api.py](bv_api.py) 顶部。

## 打包 exe

Windows 上运行：

```powershell
.\build_exe.ps1
```

生成文件：

```text
dist\bv2008BatchTool\bv2008BatchTool.exe
```

把 `dist\bv2008BatchTool` 整个文件夹发给同学即可。

## 文件结构

```text
├── bv_gui.py           # PySide6 桌面界面
├── bv_api.py           # bv2008 网关、登录、业务接口封装
├── bv_batch_runner.py  # xlsx 读取、批处理、结果写回
├── requirements.txt    # 运行与打包依赖
├── build_exe.ps1       # Windows 打包脚本
└── LICENSE
```
