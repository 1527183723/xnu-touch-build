# dada TouchCom：XNU 26 可选源码移植

状态：两个驱动对象在当前 XNU 12377 的导出头文件下编译成功；五组主机协议测试共
2393 项通过 ASAN/UBSAN。**完整内核尚未链接，也没有在 dada 的 iOS 26 上验证触摸。**
这些对象使用缓存 SDK 做编译检查，不能当作可发布内核，也不是 UEFI 镜像。

来源是此前 dada r11 的冻结源码和实机验证记录。只导入八个 TouchCom/SPI/供电文件，
通过 integration.patch 接入当前 warsaw 输入队列和停机清理；原 iOS 26 HID 服务及
GPU 源码不变。选择 dada_touch 后跳过 Goodix；未选择时保留原来的 Goodix 路径。
没有复制旧版整套 XNU，也没有猜测未发布的 UEFI shared-ring ABI。

在具有 Xcode、真实 IIG 和官方构建依赖的 macOS 上，从仓库根目录执行：

```sh
bash tools/dada/build_touch_macos.sh
```

也可只准备源码，再使用原仓库构建流程：

```sh
DADA_NATIVE_TOUCH=1 bash xnu26/apply.sh /path/to/new-xnu-tree
```

没有本地 Mac 时，可在用户自己的仓库手动运行 `.github/workflows/build-dada-touch.yml`。
该工作流只上传构建产物和日志，不推送分支、不发布 Release；本次没有上传源码或触发工作流，
macOS 完整构建入口尚未实跑。使用时需要将所需源码放入用户指定的 GitHub 仓库。

该开关默认关闭。应用脚本核对 Apple 基准提交、集成文件哈希和八个导入文件哈希；
对不匹配或部分修改的源码拒绝覆盖。已完整应用时只校验，不重复修改。

新内核链接成功并确认符号后，打包时追加：

```text
dada_touch=4 dada_touch_mmio=1 dada_touch_power=1 dada_touch_reset=1
```

单独给旧 kernel-ath12k.bin 加这些参数不会出现新驱动。iOS 26 的有效工作必须由新内核
产生真实 TouchCom 报告并经 input26-service 送达 HID 后才能确认。完成前不要用
out/dada-graphics-r3 冒充触摸修复版；那个包仍携带原来的预编译内核。

manifest.json 记录源码来源与 SHA256，touch-tests.log 和 touch-object-build.log 是本机
检查结果。linux-touch-compile.json 明确记录仅编译对象、没有链接内核。
