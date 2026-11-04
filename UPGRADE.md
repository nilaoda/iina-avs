# IINA 1.5 / FFmpeg 9 升级记录

## 版本与代码状态

- IINA：合入上游 `v1.5.0`，保留 AVS 分支的自定义 libmpv gpu-next 渲染和截图设置。
- FFmpeg：`9.0.2`；mpv：`v0.41.0`，继续应用本地 Render API gpu-next 补丁。
- 上游 mpv 的 gpu-next Render API PR [#16818](https://github.com/mpv-player/mpv/pull/16818) 在本次检查时仍未合并。
- 两个升级分支分别为 `feature/iina-1.5-ffmpeg-9` 和 `feature/ffmpeg-9.0.1`。

## Actions 验证顺序

1. 在 `mpv-iina-avs` 推送经确认的升级代码后，运行 **Build macOS media stack**，构建 arm64、x86_64 的 FFmpeg CLI 和 IINA 依赖包。需要让 IINA 工作流自动下载时，设置 `publish_release=true` 并记录生成的 release tag。
2. 在 `iina-avs` 推送经确认的升级代码后，运行 **Build IINA from custom libmpv bundle**，`bundle_repo` 使用 `nilaoda/mpv-iina-avs`，`bundle_tag` 填第一步的 tag。首次验证建议 `publish_release=false`。
3. 下载两种架构的 IINA 产物，验证启动、正常播放、AVS 系列及 Audio Vivid、HDR/Dolby Vision、PNG/JXL/AVIF 截图。

依赖包必须包含 FFmpeg 9.0.2 的动态库、对应头文件和 `bundle.json`。旧版 9.0.1 / 8.1 release 不能用于当前配置；留空 `bundle_tag` 会选择 latest，首次运行应明确指定新 tag。

`other/custom_bundle.py` 在编译前验证版本、架构和动态库依赖，同步对应头文件并生成 Xcode 动态库引用。构建后再次验证 app。FFmpeg ABI 为 avcodec/avformat/avdevice 63、avutil 61、avfilter 12、swresample 7、swscale 10。

## 2026-10-23 更新

- 合入上游 `develop` 至 `7f2c7673`，保留 AVS 分支的渲染、截图、色彩映射、下混归一化和 SDK 兼容改动。
- 工作流和依赖校验脚本默认使用 FFmpeg 9.0.2，等待 `mpv-iina-avs` 构建并发布对应的双架构依赖包后进行完整 IINA 构建验证。
- FFmpeg 9.0.2 已通过 6 个自定义补丁应用、精简 arm64 编译、AV3A 解析回归及 AVS+ / DRA 短样本解码；以下为此前 9.0.1 的完整本地验证记录。

## 2026-11-04 下混音量修复

- 取消自动添加 `audio-normalize-downmix=yes`；对已运行过旧迁移的设置，一次性移除完全匹配的 `yes` 条目，保留 `no` 等其他值以及迁移后的手动修改。旧版没有保存条目来源，因此无法区分当时手填的同值 `yes`，这类条目也会被移除。
- IINA 启用自定义 libmpv 的 `audio-limit-downmix=yes`，仅在实际发生多声道到单声道/立体声转换时限幅，避免对正常片源固定衰减。
- 需要重新构建 `mpv-iina-avs` 依赖包；`bundle.json` 必须包含 `audio_limit_downmix: true`。本地使用旧 libmpv 且没有显式下混选项时，暂时沿用旧的归一化保护并记录警告。
- 已完成精简 arm64 mpv/libmpv 编译，以及新增 C 代码的 x86_64 语法检查；复查验证了显式抖动参数生效、混音增益不重复应用。原生 mpv 输出中，春晚样本峰值保持约 -6.68 dBFS，央视频样本由 +1.88 dBFS 限制到约 -0.45 dBFS；整数输出同样受到保护。
- 立体声音轨和 5.1 输出在开关保护时数据一致；2→6→2 声道切换、AV3A/AAC 音轨切换、定位、变速及音频首尾标记验证通过。以上使用文件或空音频输出；完整 App、真实设备和长片听感仍需验证。

## 已完成的本地验证

- arm64 的 FFmpeg 9.0.1、自定义解码器和 libmpv 编译、安装、可重定位打包通过。
- 4 个 FFmpeg 补丁与 4 个 mpv 补丁可以依次应用到干净的对应版本源码。
- `/Volumes/SSD/Samples` 中 9 个样本各解码 30 帧通过：AVS+、AVS+_P、AVS2、AVS3、DRA、AV3A TS、两个 Audio Vivid MP4、AV1。
- OpenGL/libmpv gpu-next 实际渲染 SDR、Dolby Vision P5、AV1、AVS2 HLG，通过 PNG/JXL/AVIF 共 12 张截图；P5 和 AV1 使用 VideoToolbox。截图也通过 FFmpeg 解码检查。
- 修正 SVT-AV1 的 AVIF 像素格式选择，以及 Lanczos/Hermite 缩放器查找；AVS 无效序列头现在返回错误，避免忽略非法尺寸后崩溃。
- FFmpeg CLI 音视频往返、AVIF 编码、动态库架构及依赖闭包检查通过。Actions 配置通过 actionlint；自定义 Objective-C FFmpeg 调用通过语法检查。

以上是短样本回归与渲染测试，不代表完整影片、所有 HDR 色彩模式或完整 IINA 功能均已验证。完整 IINA 编译和 x86_64 运行留给 Actions：本机 Xcode 16.4 无法处理上游新图标资源格式，工作流使用 Xcode 26.5。

## 本地产物及清理

测试包、截图、解码帧校验和、构建日志及报告保存于 `/Volumes/SSD/iina-avs-upgrade-1.5`。

本机测试包受已有 Homebrew 二进制依赖影响，最低系统要求至少为 macOS 15，不能据此承诺上游源码配置的 macOS 12 兼容性。正式产物应以 Actions 构建结果和目标系统实测为准。

本次临时目录 `mpv-iina-avs/.work/upgrade-9` 已删除，本次新装的 11 个 Homebrew 包已卸载；原有依赖和其他历史测试目录保留。为节省系统盘，测试生成的 `iina-avs/deps/lib` 中 74 个动态库副本也已删除；匹配头文件与 Xcode 项目修改保留，Actions 会重新填充动态库。清理后系统盘可用空间约 17 GiB。
