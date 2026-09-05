//%attributes = {}
$ratio:=Get system DPI(DPI_RATIO)
$dpi:=Get system DPI(DPI_VALUE)

ALERT:C41(String:C10($ratio)+"% which is "+String:C10($dpi)+"DPI")