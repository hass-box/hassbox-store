# HassBox 集成商店

平替 HACS，无需 Github 账号，小白轻松安装 Home Assistant 集成、卡片和主题样式。

## 安装/更新

#### 方法 0: 联系客服安装

啥也不会的小白，可以至 HassBox 微信公众号联系客服，我们免费远程帮你安装！

#### 方法 1: 通过`Samba`或`SFTP`手动安装

下载`hassbox_store.zip`并解压，复制`hassbox_store`文件夹到 Home Assistant 根目录下的`custom_components`文件夹中。

#### 方法 2: 通过`SSH`或`Terminal & SSH`加载项执行一键安装命令

```shell
curl -fsSL get.hassbox.cn/hassbox-store | bash
```

#### 方法 3: 通过`shell_command`服务

1. 复制下面的代码到 Home Assistant 配置文件`configuration.yaml`中

   ```yaml
   shell_command:
     update_hassbox_store: |-
       curl -fsSL get.hassbox.cn/hassbox-store | bash
   ```

2. 重启 Home Assistant

3. 在 Home Assistant 开发者工具中调用此服务[`service: shell_command.update_hassbox_store`](https://my.home-assistant.io/redirect/developer_call_service/?service=shell_command.update_hassbox_store)

## 使用教程

[HassBox 集成商店使用演示 -> ](https://hassbox.cn/service/integration/install-hassbox-store.html#%E4%BD%BF%E7%94%A8%E6%BC%94%E7%A4%BA)
