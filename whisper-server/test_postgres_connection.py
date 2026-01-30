"""
PostgreSQL 连接测试脚本
"""
import psycopg2

# 数据库配置
config = {
    'host': '83.229.124.177',
    'port': '5432',
    'database': 'ytb_subtitles',
    'user': 'ytb_subtitles',
    'password': '3rnmdw4D3EYTkPRZ'
}

print("=" * 60)
print("PostgreSQL 连接测试")
print("=" * 60)
print(f"主机: {config['host']}")
print(f"端口: {config['port']}")
print(f"数据库: {config['database']}")
print(f"用户: {config['user']}")
print(f"密码: {'*' * len(config['password'])}")
print("=" * 60)

try:
    print("\n正在尝试连接...")
    conn = psycopg2.connect(
        host=config['host'],
        port=config['port'],
        database=config['database'],
        user=config['user'],
        password=config['password'],
        connect_timeout=10
    )
    
    print("✓ 连接成功!")
    
    # 测试查询
    cursor = conn.cursor()
    cursor.execute("SELECT version();")
    version = cursor.fetchone()
    print(f"\n数据库版本: {version[0]}")
    
    # 列出所有表
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
    """)
    tables = cursor.fetchall()
    print(f"\n当前数据库中的表 ({len(tables)} 个):")
    for table in tables:
        print(f"  - {table[0]}")
    
    cursor.close()
    conn.close()
    
    print("\n✓ 测试完成!")
    
except psycopg2.OperationalError as e:
    print(f"\n✗ 连接失败 (OperationalError):")
    print(f"  {e}")
    print("\n可能的原因:")
    print("  1. 服务器地址或端口错误")
    print("  2. 数据库不存在")
    print("  3. 用户名或密码错误")
    print("  4. 防火墙阻止连接")
    print("  5. PostgreSQL 服务未启动")
    
except Exception as e:
    print(f"\n✗ 发生错误:")
    print(f"  {type(e).__name__}: {e}")

print("=" * 60)
