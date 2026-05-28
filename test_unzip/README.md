# AstrBot Danbooru 图片下载器插件

## 安装方法

1. 将整个 `astrbot_danbooru_downloader` 文件夹复制到 AstrBot 的插件目录
2. 在 AstrBot WebUI 中启用插件
3. 配置你的 Danbooru 用户名和 API Key

## 获取 Danbooru API Key

1. 登录 Danbooru 账号
2. 进入 Profile 设置页面
3. 找到 "API Key" 部分，点击生成
4. 复制生成的 API Key

## 使用方法

1. 在插件配置中填入 Danbooru 用户名和 API Key
2. 在插件页面中选择要下载的角色
3. 设置下载数量和并发数
4. 点击"开始下载"
5. 下载完成后，在 `data/plugins/astrbot_danbooru_downloader/downloads` 目录查看图片

## 功能特点

- ✅ 支持选择多个角色批量下载
- ✅ 并发下载加速
- ✅ 实时显示下载进度
- ✅ 自动过滤非图片文件
- ✅ 只下载全年龄内容（rating:g）
- ✅ 错误处理和日志记录

## 角色列表

插件内置了以下角色的标签映射：

### Re:Zero
- 拉姆 (rem)
- 雷姆 (ram)

### 原神
- 胡桃 (hutao)
- 雷电将军 (raiden_shogun)
- 神里绫华 (kamisato_ayaka)

### 电锯人
- 玛奇玛 (makima)
- 帕瓦 (power)

### 其他热门角色
- 02 (Darling in the Franxx)
- 阿尼亚 (间谍过家家)
- 约尔 (间谍过家家)

### 明日方舟：终末地
- 管理员
- 艾尔黛拉
- 佩丽卡
- 陈千语

## 文件结构

```
astrbot_danbooru_downloader/
├── main.py                      # 插件主文件
├── _conf_schema.json           # 配置界面定义
└── pages/
    └── danbooru-downloader/
        └── index.html          # 插件Web界面
```

## 注意事项

- 请勿在公共场合泄露你的 API Key
- Danbooru 有 API 访问频率限制，请合理设置并发数
- 下载的图片仅供个人学习使用，请遵守版权规定
