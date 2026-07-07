# 线上备案申请成功基线

更新时间：2026-06-18

## 目标

固化“线上备案申请”在当前测试账号与当前页面结构下的可重复自动化成功路径，作为：

- 脚本回归基线
- 后续多模型复跑基线
- 第二阶段 Python 执行子系统的原型样本

## 已验证成功的页面路径

1. `线上备案申请`
2. `我要申请备案`
3. `拟备案信息纳入溯源系统`
4. `签署溯源服务协议`
5. `同意签署`
6. `纳入溯源系统的拟备案信息录入`
7. 初始申请表选择 `备案品种` 与 `备案类型`
8. 展开完整育苗表单
9. 填写完整育苗数据并上传必须附件
10. `信息纳入溯源系统`
11. 在最终弹窗中选择 `备案登记/监管单位` 并上传 `申请表`
12. `提交备案`

## 成功判定

同时满足以下至少一项：

1. 页面出现文案：`提交完成，待备案登记/监管单位登记备案`
2. 最终接口 `POST /prod-api/zwsy/registration/apply/dept` 返回：
   - `{"code":200,"msg":"操作成功"}`

## 初始表单基线值

- 备案品种：`墨兰`
- 备案类型：`育苗`

## 完整育苗表单模板值

这些值来自 2026-06-18 已手工成功并被自动化复用验证的记录。

```json
{
  "deptId": "100",
  "cityRegionId": "4501-450103-450103004-450103004014",
  "cityRegionName": "南宁-青秀-南湖-厢竹社区居委会",
  "rangeStr": "街心花园",
  "seedlingSource": 0,
  "cultivateType": 2,
  "cultivateDate": "2026-06-01",
  "cultivateNum": 120,
  "cultivateArea": 8,
  "remark": "自动化回填测试",
  "acceptanceNum": 120,
  "cultivatePurpose": 2,
  "acceptanceDeptId": "100",
  "acceptancePerson": "王五",
  "acceptanceDate": "2026-06-17",
  "isHardening": 0
}
```

## 最终备案提交基线值

- 备案登记/监管单位 ID：`100`
- 备案登记/监管单位名称：`广西壮族自治区林业局`

## 本次验证产生的关键记录

### 参考成功样本

- 已存在成功记录 ID：`101371731601000105`

### 自动化新建并提交成功的记录

- 自动化保存返回 ID：`101378368363000187`

## 关键接口序列

### 阶段一：纳入溯源系统

1. 上传育苗人员信息表
   - `POST /prod-api/common/upload`
2. 上传验收文件
   - `POST /prod-api/common/upload`
3. 保存拟备案信息
   - `POST /prod-api/zwsy/registration/apply/save`

成功响应示例：

```json
{"code":200,"msg":"操作成功","data":"101378368363000187"}
```

### 阶段二：提交备案

1. 上传申请表
   - `POST /prod-api/common/upload`
2. 最终提交备案
   - `POST /prod-api/zwsy/registration/apply/dept`

成功响应示例：

```json
{"code":200,"msg":"操作成功"}
```

## 关键产物

### 中间探针

- [online_apply_min_submit_probe_v2.json](C:\project\aut_agent\artifacts\suyuan_submit_loop\online_apply_min_submit_probe_v2.json)
- [online_apply_full_submit_probe.json](C:\project\aut_agent\artifacts\suyuan_submit_loop\online_apply_full_submit_probe.json)
- [online_apply_submit_record_probe.json](C:\project\aut_agent\artifacts\suyuan_submit_loop\online_apply_submit_record_probe.json)

### 关键截图

- [online_apply_min_submit_v2_after.png](C:\project\aut_agent\artifacts\suyuan_submit_loop\online_apply_min_submit_v2_after.png)
- [online_apply_full_submit_after.png](C:\project\aut_agent\artifacts\suyuan_submit_loop\online_apply_full_submit_after.png)
- [online_apply_submit_record_after.png](C:\project\aut_agent\artifacts\suyuan_submit_loop\online_apply_submit_record_after.png)

## 脚本入口

固化脚本：

- [tools/suyuan_submit_loop.py](C:\project\aut_agent\tools\suyuan_submit_loop.py)

当前脚本依赖：

- 已登录的可见 Chrome，会话通过 `http://localhost:9222` 连接
- 当前账号具备 `线上备案申请` 所需权限
- `demo/.env` 或 `demo/local_qwen.env` 中可加载模型配置

## 注意事项

1. 这条成功路径使用了“模板回填”方式，不是通用智能探索。
2. 当前脚本默认复用这组已验证模板值，用于稳定回归。
3. 附件上传文件目前使用脚本运行目录中自动生成的 PDF，占位用于验证链路，不代表真实业务文件格式最优解。
4. 如果页面结构或字段枚举变化，优先更新基线文档与 `SUCCESS_BASELINE` 常量，而不是直接堆更多临时分支。
