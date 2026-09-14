# 云效固定版本目录读取

状态：本地实现与合成回归通过；真实调用及实例激活单独记录，不由单测替代。

依据阿里云 [ListFiles 官方接口](https://help.aliyun.com/zh/yunxiao/developer-reference/listfiles)，
新增 `codeup.tree.list`。能力沿用 `codeup.read.v1` 的固定中心站点、GET、`code.read`、
L2、NONE 副作用、RESTRICTED 数据分类及既有仓库 ACL。旧四项只读能力保持兼容。

已有授权绑定后调用普通控制面接口：

```text
POST /api/v1/code/repositories/{local-repository-id}/remote-read
{"operation":"codeup.tree.list","commit_id":"<40位固定提交>","path":""}
```

此 POST 仅是控制面的受治理查询入口，网关向云效只发 GET。不支持可变分支名、递归参数、
自选域名、供应商仓库 ID 或内联凭据。`path` 为空读取根目录，子目录需要新的授权调用。

返回每个项目的 commit、父目录、路径、Git object id、mode、type、LFS 标记与
`can_read_content`。普通文件可进一步通过 `codeup.file.read` 读取；该标记只是读取条件
提示，每次实际读取仍须重新授权。子模块、LFS、符号链接与禁读文件保留元数据，不能
因删除这些条目而把剩余内容标为整个仓库已完整读取。不会跟随链接、下载 LFS 或执行文件。

DIRECT 返回必须是当前目录直接子项，名称/路径/类型/mode/object id 都须匹配。重复、
越界或非法响应失败关闭。最多 2000 项、2 MiB 响应；恰好达到条数上限时 complete=false，
不伪造厂商分页。`complete=true` 只表示本次目录响应完整，不证明整个仓库已遍历，也不
是 Git tree 的独立密码学验证。官方文档未明确分页和部分响应的全部行为，真实试点应
独立记录返回形式及预算边界，未知形式保持失败而非猜测解析。

能力登记和绑定是本机控制面配置，云效保持只读。不能因拥有仓库读取权限而默认获准
修改治理配置；激活应满足该实例 Policy 的明确许可，不能修改策略来隐藏拒绝记录。

工作台中选择授权仓库和“目录浏览”，填写完整提交编号，目录路径留空即为根目录。
打开子目录、读取普通文件和返回父目录都沿用同一提交；每次导航都会重新检查权限。
不支持读取的条目仍可见并标注原因。空目录、目录上限和权限失败分别展示；切换仓库、
提交或路径时会清除旧结果，迟到响应不能覆盖新的查询。
