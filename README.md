# Personal Hermes

个人版本自动接收官方正式版，并保留经过验证的个人修复。

- [应用源码在 personal/stable](https://github.com/YusenZhang0601/hermes-agent/tree/personal/stable)。
- [GitHub 云端每六小时检查、合并和验证](https://github.com/YusenZhang0601/hermes-agent/actions/workflows/personal-schedule.yml)。通过后发布到应用分支。
- 本机 Hermes 通过软件内更新按钮安装已发布的个人版本。
- 个人修复包含辅助压缩鉴权，以及本地、局域网和 Tailscale 模型元数据探测的代理处理。
  两项行为均进入每次正式版更新前的回归检查。

此默认分支仅提供定时入口。实际同步流程固定引用已验证提交，应用源码和贡献分支独立维护。

取得应用源码时指定应用分支。

```sh
git clone --branch personal/stable https://github.com/YusenZhang0601/hermes-agent.git
```
