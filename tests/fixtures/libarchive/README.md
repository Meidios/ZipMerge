# RAR 回归样本

这两个 uuencode 文本来自 libarchive 的公开测试套件，仅在测试运行时解码到临时目录，不进入 ZipMerge 安装包：

- `rar4-basic.rar.uu`：[`test_read_format_rar.rar.uu`](https://github.com/libarchive/libarchive/blob/a4ba070a01e967f7b98998c654dcc082e16fe58c/libarchive/test/test_read_format_rar.rar.uu)，解码后 336 字节，SHA-256 `d421b86f6290aefad61b2a36737253b2b30fe27c156bd95abfc230f24fe0307e`；
- `rar5-stored.rar.uu`：[`test_read_format_rar5_stored.rar.uu`](https://github.com/libarchive/libarchive/blob/a4ba070a01e967f7b98998c654dcc082e16fe58c/libarchive/test/test_read_format_rar5_stored.rar.uu)，解码后 109 字节，SHA-256 `35d75e315d164d2e329afc28f7d844f013271b4fcffd4ddd78efcdd114a383a7`。

上游：<https://github.com/libarchive/libarchive>，固定版本 `a4ba070a01e967f7b98998c654dcc082e16fe58c`。

## 许可证

Copyright (c) 2003-2018 <author(s)>
All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR(S) ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE AUTHOR(S) BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
