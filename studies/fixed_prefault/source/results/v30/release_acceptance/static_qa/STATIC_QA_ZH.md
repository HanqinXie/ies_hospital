# 最终ZIP独立静态QA

状态：passed；17项检查；发现问题0项。ZIP为98,896,789 bytes、1877成员，SHA256：`48a93fcd391f9cbf903d1e6185fc12916ac91a219c75e0bd2c87bf420b96e54d`。

C02的858个科学源码和36个原输入，已分别在ZIP及独立解压project内逐SHA核对，全部一致；额外project文件仅文档、既有CSV/JSON夹具等，无新增未冻结Python/MATLAB。两处正式工程报告SHA相同，两处主表SHA相同。

两源PDF未放入紧凑ZIP，README、SOURCE_PDFS和PACKAGING_OMISSIONS均明确披露；D原件SHA仍吻合。完整分页全文、两个映射、所有既留方程/表格PNG、冻结原型及实际审计manifest逐项核对无缺失。该ZIP不是包含原PDF字节的完全自包含文献包。

标准库stock zipfile已直接读取上述源码/输入与审计成员，未替换解压器；1858个ZIP-LZMA成员本地头属性全部为标准5字节属性、64MiB字典，另外19成员stored。外manifest与中央目录成员/bytes一致；index历史support完整保留，省略表与实际ZIP无矛盾重叠。

本次未扫描正在恢复的原生runs，未重复全量87k文件散列或全ZIP CRC，未调用模型/求解器，未改ZIP、source或恢复目录。全量原生恢复、只读重放和真实public15验收由root另外完成，本静态通过不代替它们。

具体逐项结果和计数见STATIC_QA.json；实际命令及退出码见COMMAND_RECEIPT.json。
