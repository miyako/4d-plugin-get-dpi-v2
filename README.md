# 4d-plugin-get-dpi-v2
Get system DPI on Windows

Must read 

https://docs.microsoft.com/ja-jp/windows/desktop/hidpi/high-dpi-desktop-application-development-on-windows

You must restart 4D if DPI was changed during session.

### Examples

```
$ratio:=Get system DPI (DPI_RATIO)
$dpi:=Get system DPI (DPI_VALUE)

ALERT(String($ratio)+"% which is "+String($dpi)+"DPI")
```

<img width="681" alt="2018-12-20 17 12 47" src="https://user-images.githubusercontent.com/1725068/50272824-4f09aa00-047c-11e9-8773-0b57cf915744.png">

<img width="693" alt="2018-12-20 17 15 05" src="https://user-images.githubusercontent.com/1725068/50272838-5fba2000-047c-11e9-8e9e-ababa5753128.png">
