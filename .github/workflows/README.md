# GitHub Actions 自动构建

`.github/workflows/build.yml` 配置了自动构建流水线：每次推送代码或打 tag，
GitHub 会在云端自动打包出 Windows 安装包，你不需要在本地跑任何命令。

## 它做了什么

| 步骤 | 说明 |
|---|---|
| 1. 检出代码 | 把仓库拉到云端 |
| 2. 装 Python 依赖 | pyinstaller / tkinterdnd2 |
| 3. 装 Inno Setup | 编译安装包的工具 |
| 4. 下载 ffmpeg | 约 200MB（不能提交进仓库，只能现下） |
| 5. 生成图标 | 调用 `installer/make_icon.py` |
| 6. 跑测试 | 核心逻辑 / GUI / 界面状态 |
| 7. 打包 exe | PyInstaller |
| 8. 编译安装包 | Inno Setup，产出 `dashcam-transcoder-x.y.z-setup.exe` |
| 9. 上传产物 | 存为可下载的 artifact |
| 10. 发到 Releases | **仅打 tag 时执行** |

## 怎么触发

### 方式一：推 tag（会发到 Releases）

```bash
git tag v1.0.0
git push origin v1.0.0
```

约 10 分钟后，仓库的 Releases 页面会出现 `dashcam-transcoder-1.0.0-setup.exe`。

### 方式二：手动触发

打开仓库 → **Actions** 标签 → 左侧选「构建安装包」→ 右上角 **Run workflow** → 选分支 → 运行。

这种方式只会生成构建产物，不会发 Releases。

### 方式三：推送代码（自动验证）

往 `main` 推代码也会触发（相当于每次推送都验证一遍构建是否正常），
以及任何指向 `main` 的 Pull Request 都会触发。

## 在哪看运行结果

仓库 → **Actions** 标签 → 点某次运行 → 可以逐步骤看日志。
如果失败了，点开失败的步骤，日志会写明原因。

## 一些约定

- **只在 tag 发布**：普通 push 只构建，不发 Releases，避免刷出一堆版本。
- **ffmpeg 不入库**：200MB 远超 GitHub 单文件 100MB 限制，必须构建时下载。
- **安装包不入库**：同理，125MB 的安装包只在 Releases 里，不进 git。
- **测试没有视频样本会自动跳过**：仓库不含任何用户视频（隐私），
  所以需要样本的用例会显示 `[SKIP]` 而不是失败，不会中断流水线。

## 常见问题

**构建慢？** 正常。整个流程约 8-12 分钟，其中下载 ffmpeg 占 2-3 分钟，
Inno Setup 压缩那个 125MB 安装包要占约 2 分钟。

**Release 是空的？** 检查是不是推了 tag（方式一）。普通 push 不发 Release。

**想改安装包里的名字或图标？** 改 `installer/dashcam_transcoder.iss`，
路径用 `/D` 参数传入，不要写死绝对路径。
