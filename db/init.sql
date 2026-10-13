-- =============================================================
-- AI 视觉质检系统 - 数据库初始化脚本
-- 执行方式：docker exec -i mysql-ai mysql -uroot -p123456 < init.sql
-- =============================================================

-- 1. 建库
CREATE DATABASE IF NOT EXISTS ai_quality
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE ai_quality;

-- 2. 建表：质检记录表
CREATE TABLE IF NOT EXISTS inspect_records (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键，自增',
  filename    VARCHAR(255) NOT NULL               COMMENT '上传的图片文件名',
  result      VARCHAR(16)  NOT NULL               COMMENT '质检结果：缺陷 / 正常',
  confidence  DECIMAL(5,4) NOT NULL               COMMENT '置信度，0.0000 ~ 1.0000',
  defect_type VARCHAR(64)  DEFAULT NULL           COMMENT '缺陷类型，正常时为 normal',
  image_url   VARCHAR(512) DEFAULT NULL           COMMENT '图片访问地址（/static/xxx.jpg）',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '质检时间',
  PRIMARY KEY (id),
  KEY idx_created_at (created_at),
  KEY idx_result (result)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='产品外观质检历史记录表';

-- 3. 创建应用专用账号（最小权限原则，只授权 ai_quality 库）
CREATE USER IF NOT EXISTS 'aiuser'@'%' IDENTIFIED BY 'aiuser123';
ALTER USER 'aiuser'@'%' IDENTIFIED BY 'aiuser123';
GRANT ALL PRIVILEGES ON ai_quality.* TO 'aiuser'@'%';
FLUSH PRIVILEGES;

-- 4. 验证
SHOW DATABASES;
USE ai_quality;
SHOW TABLES;
DESC inspect_records;
SELECT '数据库初始化完成' AS msg;
