# PKU Radar

一个帮我筛选北大通知的小工具。它从[北大知道](https://pkuknow.cn/)获取通知，根据个人兴趣用 LLM 排序，再生成终端摘要；也可以选择通过邮件发送。

## 功能

- 拉取通知并去重，用 SQLite 保存处理状态
- 根据个人偏好评估通知，筛选值得关注或采取行动的内容
- 在终端生成摘要，可选邮件发送
- 记录运行结果，方便排查问题

## 运行

需要 Python 3.11+。在项目根目录安装依赖：

```powershell
python -m pip install -e .
python -m pku_radar run --fake-day 1
```

离线模式使用仓库中的测试数据，不需要网络或 LLM。个人偏好可以在 `config/preferences.yaml` 中调整。

运行测试：

```powershell
python -m unittest discover -s tests -v
```

## 实时运行

实时模式需要在当前终端设置以下环境变量：

- `LLM_BASE_URL`：兼容 OpenAI API 的服务地址，例如以 `/v1` 结尾
- `LLM_API_KEY`：API 密钥
- `LLM_MODEL`：模型名称

然后运行：

```powershell
python -m pku_radar run --live
```

如需邮件发送，再配置 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASSWORD` 和 `RADAR_EMAIL_TO`，并添加 `--email` 参数。项目不会自动读取 `.env` 文件，请勿把密钥提交到仓库。

## Tip

本来只是想做个工具，帮我收集北大值得关注的信息，结果意外发现了一个好网站：[北大知道](https://pkuknow.cn/)！

看到这里的全体 PKUer，务必收藏这个网站！
