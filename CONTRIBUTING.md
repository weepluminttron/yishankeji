# 贡献指南

感谢你关注「光纤行业获客助手」！欢迎提 Issue 和 PR。

## 提 Issue

- 先搜索是否已有相同或相似的 Issue。
- 描述问题请尽量包含：复现步骤、预期行为、实际行为、运行环境（系统 / Python 版本 / 部署方式）。
- 功能建议请说明使用场景与价值。

## 分支规范

- 主分支：`main`（或 `master`）。
- 功能/修复请用描述性分支名，例如：
  - `feat/xxx` 新功能
  - `fix/xxx` 缺陷修复
  - `chore/xxx` 工程化 / 文档

## 本地运行

参考 [README.md](./README.md)：

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
python server.py
```

## Pull Request 约定

1. 基于最新 `main` 创建你的分支。
2. 一个 PR 聚焦一件事，标题简洁明了。
3. 改动后请本地跑一遍 `python -m compileall -q core server.py scripts` 确保无语法错误。
4. 涉及配置/部署的改动，请在 PR 描述里说明影响范围。
5. 提交前确认没有把 `server.env`、数据库 `data/`、密钥等敏感文件提交进来（已在 `.gitignore` 中忽略）。

## 代码风格

- 保持与现有代码一致的缩进与命名风格（4 空格缩进，函数/变量小写下划线）。
- 中文注释为主，保持简洁。
