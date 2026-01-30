# PostgreSQL 数据库迁移指南

## 迁移概述

本文档说明如何将 YouTube Live Subtitles 项目从 MongoDB/Supabase 迁移到 PostgreSQL 数据库。

## 数据库配置信息

### PostgreSQL 连接信息
- **主机**: 83.229.124.177
- **端口**: 5432
- **数据库名**: ytb_subtitles
- **用户名**: ytb_subtitles
- **密码**: 3rnmdw4D3EYTkPRZ

## 已完成的工作

### 1. 创建 PostgreSQL 数据库模块
✅ 文件: `db/postgres_db.py`
- 实现了连接池管理
- 自动创建数据表和索引
- 提供 `get_by_video_id()` 和 `upsert_subtitles()` 方法
- 支持 JSONB 格式存储字幕数据

### 2. 更新依赖
✅ 文件: `requirements.txt`
- 添加了 `psycopg2-binary` 依赖

### 3. 更新环境配置
✅ 文件: `.env`
- 添加了 PostgreSQL 连接配置
- 保留了原有的 MongoDB 和 Supabase 配置作为备份

### 4. 修改服务器代码
✅ 文件: `server.py`
- 添加了 `init_postgres()` 函数
- 修改了 `get_cached_subtitles()` 优先查询 PostgreSQL
- 修改了 `save_subtitles_cache()` 优先保存到 PostgreSQL
- 在服务启动时初始化 PostgreSQL 连接

### 5. 创建数据迁移脚本
✅ 文件: `migrate_to_postgres.py`
- 支持从 MongoDB 迁移数据
- 支持从 Supabase 迁移数据
- 支持从本地缓存文件迁移数据
- 包含迁移验证功能

### 6. 创建连接测试脚本
✅ 文件: `test_postgres_connection.py`
- 用于测试 PostgreSQL 连接
- 显示数据库版本和表信息

## 数据库表结构

### subtitles 表
```sql
CREATE TABLE IF NOT EXISTS subtitles (
    id SERIAL PRIMARY KEY,
    video_id VARCHAR(255) UNIQUE NOT NULL,
    language VARCHAR(50),
    target_lang VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    subtitles JSONB NOT NULL
)
```

### 索引
- `idx_video_id`: 在 video_id 字段上创建索引
- `idx_created_at`: 在 created_at 字段上创建索引

## 迁移步骤

### 前置条件检查

1. **检查网络连接**
   ```powershell
   Test-NetConnection -ComputerName 83.229.124.177 -Port 5432
   ```

2. **确认 PostgreSQL 服务运行**
   - 确保远程 PostgreSQL 服务器正在运行
   - 确认防火墙允许来自您 IP 的连接
   - 确认 PostgreSQL 配置允许远程连接 (pg_hba.conf)

### 执行迁移

1. **安装依赖**
   ```powershell
   pip install psycopg2-binary
   ```

2. **测试连接**
   ```powershell
   python test_postgres_connection.py
   ```
   
   如果连接失败,请检查:
   - 服务器 IP 和端口是否正确
   - 数据库名称、用户名、密码是否正确
   - 网络防火墙设置
   - PostgreSQL 服务器的 `postgresql.conf` 中 `listen_addresses` 设置
   - PostgreSQL 服务器的 `pg_hba.conf` 中的访问控制规则

3. **运行迁移脚本**
   ```powershell
   python migrate_to_postgres.py
   ```
   
   迁移脚本会:
   - 从 MongoDB 读取所有数据并迁移到 PostgreSQL
   - 从 Supabase 读取所有数据并迁移到 PostgreSQL
   - 从本地缓存文件读取数据并迁移到 PostgreSQL
   - 自动去重(基于 video_id)
   - 生成迁移日志 `migration.log`

4. **验证迁移结果**
   迁移脚本会自动验证并显示:
   - PostgreSQL 中的总记录数
   - 随机抽样验证数据完整性

## 故障排除

### 连接超时问题

如果遇到连接超时,可能的原因:

1. **防火墙阻止**
   - 检查本地防火墙设置
   - 检查服务器端防火墙设置
   - 联系服务器管理员开放端口 5432

2. **PostgreSQL 配置问题**
   
   服务器端需要修改 `postgresql.conf`:
   ```conf
   listen_addresses = '*'  # 或指定 IP
   port = 5432
   ```
   
   服务器端需要修改 `pg_hba.conf`:
   ```conf
   # 允许远程连接
   host    ytb_subtitles    ytb_subtitles    0.0.0.0/0    md5
   ```

3. **网络问题**
   - 使用 telnet 或 nc 测试端口连通性
   - 检查是否有 VPN 或代理影响连接

### 手动迁移方案

如果自动迁移脚本无法连接,可以:

1. **导出本地数据**
   - 所有缓存数据都在 `cache/` 目录下的 JSON 文件中
   - 可以手动收集这些文件

2. **在服务器端导入**
   - 将 JSON 文件上传到服务器
   - 在服务器本地运行迁移脚本

## 运行服务

迁移完成后,正常启动服务:

```powershell
python server.py
```

服务会:
1. 优先连接 PostgreSQL
2. 如果 PostgreSQL 连接失败,回退到 MongoDB
3. 如果都失败,使用本地缓存模式

## 数据流程

### 读取优先级
1. 本地缓存文件 (最快)
2. PostgreSQL 数据库
3. MongoDB 数据库 (备用)

### 写入策略
1. 始终写入本地缓存
2. 同时写入 PostgreSQL (如果连接成功)
3. 同时写入 MongoDB (如果连接成功,作为备份)

## 性能优化建议

1. **连接池配置**
   - 当前设置: 最小 1 个连接,最大 10 个连接
   - 可根据实际负载调整 `postgres_db.py` 中的连接池参数

2. **索引优化**
   - 已创建 video_id 和 created_at 索引
   - 如需按其他字段查询,可添加相应索引

3. **JSONB 查询**
   - PostgreSQL 的 JSONB 类型支持高效的 JSON 查询
   - 可使用 GIN 索引进一步优化 JSONB 字段查询

## 备份建议

1. **定期备份 PostgreSQL**
   ```bash
   pg_dump -h 83.229.124.177 -p 5432 -U ytb_subtitles -d ytb_subtitles > backup.sql
   ```

2. **保留本地缓存**
   - `cache/` 目录包含所有字幕数据的本地副本
   - 定期备份此目录

3. **双写策略**
   - 当前配置同时写入 PostgreSQL 和 MongoDB
   - 提供了数据冗余保护

## 监控和日志

- 服务日志: `server.log`
- 迁移日志: `migration.log`
- 建议定期检查日志中的错误信息

## 联系支持

如遇到问题:
1. 检查 `server.log` 和 `migration.log` 中的错误信息
2. 运行 `test_postgres_connection.py` 诊断连接问题
3. 确认服务器端 PostgreSQL 配置正确

---

**迁移完成日期**: 2026-01-30
**文档版本**: 1.0
