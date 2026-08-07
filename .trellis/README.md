# 开发资料入口

`.trellis/` 是本仓库开发资料的统一入口。运行时源码和用户使用说明不在这里：请分别查看仓库根目录的代码和 [README.md](../README.md)。

## 当前开发规范

- [工作流](workflow.md)：任务阶段、执行顺序和完成要求。
- [后端规范索引](spec/backend/index.md)：修改插件代码前应阅读的项目规范。
- [思考指南](spec/guides/index.md)：跨层变更、复用与评审时的检查要点。

## 日常检查

在 AstrBot 源码目录可用时，替换下列占位路径后运行：

```powershell
$env:PYTHONPATH='<AstrBot 源码目录>'
& '<AstrBot 源码目录>\.venv\Scripts\python.exe' -m pytest -q
```

涉及代码变更时，还应按 [后端质量规范](spec/backend/quality-guidelines.md) 完成相应检查。
