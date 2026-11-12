# dada TouchCom：用户触摸修复说明包优先移植

主来源：用户提供的 `触摸修复说明包-20261110.zip`。该包是在
superboyyy/hakios-arm64e-dada 的 dada 原生 TouchCom 实现上修订的快照。
上游基准为 `f1ae906fcc43ba8f763663257724ba938fb820ab`；许可证 APSL-2.0。
来源文件、压缩包与修改后文件的 SHA256 记录在 manifest.json。

这是一份可选的 XNU26 源码覆盖层。用户授权绕过 IIG 后，Linux 实验版 r3
已完成完整内核链接和启动载荷打包（`out/dada-touch-experimental-r3-20261110/`）。
该版使用 DriverKit 兼容实现。2026-11-10 实机已进入 iOS 26 锁屏/桌面，用户确认
触摸点按、滑动有响应；其他外设与完整多指功能不能据此认定全部验证通过。
正常的真实 IIG 构建路径继续保留。

## 接入内容

- 优先采用压缩包的 `tcm_payload_complete()`、`tcm_read()`：校验结束字节，
  处理续读和旧 pending 状态。
- 通过 `redwood_input_init()` 选择 dada TouchCom，跳过不匹配的 Goodix 探测。
- 保留 Warsaw 的 24 字节触点队列和已核对的 23G90 HID ABI；将包内
  “孤立 MOVE/UP 不作为新接触”的逻辑接入现有独立 `input26-service`。
  不另开 inject26 输入线程争抢同一个内核队列。
- 补上工作线程退出、触点释放、引脚恢复和停止失败检测。共享电源/时钟投票
  保持开启，不在停止时关闭可能被其他外设使用的供电。
- 对畸形/截断的触点配置和报告做边界检查，限制循环次数；不生成模拟滑动。
- dada 模式关闭五指返回；打包时不得增加 `loader_return_after` 自动返回参数。

用户包的 `inject26.c` 使用另一份私有 HID 函数声明。当前版本保留 Warsaw 已核对
的声明，不能将该文件整个替换到本项目。包内 `caenv=CA_DISABLE_ASYNCHRONOUS=1`
是其原环境的渲染兼容参考，本次不替换当前已验证的 GPU/图标注入库。

## 完整构建

具备本项目所需 SDK、真实 IIG 的 macOS/Xcode 环境：

```sh
DADA_NATIVE_TOUCH=superboyyy bash tools/dada/build_touch_macos.sh
```

仅准备一个新的正常 Warsaw 源码树并应用此覆盖层：

```sh
DADA_NATIVE_TOUCH=superboyyy bash xnu26/apply.sh /path/to/new-xnu-tree
```

原 r11 覆盖层保留于 `../dada-touch/`，选择 `DADA_NATIVE_TOUCH=1` 可独立构建。
两个覆盖层互斥；应用脚本会拒绝覆盖不同或部分修改的集成文件。

完整内核链接成功后才可替换 payload 内的 kernel：

```text
dadatouch=1 touch_poll_us=4000
```

不带 `dadatouch_step`，不带 `autoswipe`。原来的 `touchscreen=2` 应移除，避免
让后续维护者误认为仍使用 Goodix。`dadatouch_careful=1` 仅用于分步硬件诊断，
默认每步暂停 2.5 秒；不是正常使用所必需。

给旧预编译内核加 `dadatouch=1` 不会安装此驱动。
