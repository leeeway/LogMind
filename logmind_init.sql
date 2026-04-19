-- Create Database
CREATE DATABASE logmind;

CREATE TABLE alert_history (
	alert_rule_id VARCHAR(36), 
	analysis_task_id VARCHAR(36), 
	tenant_id VARCHAR(36) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	severity VARCHAR(20) NOT NULL, 
	message TEXT NOT NULL, 
	notify_result TEXT NOT NULL, 
	fired_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE alert_rule (
	business_line_id VARCHAR(36) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	description TEXT NOT NULL, 
	rule_type VARCHAR(30) NOT NULL, 
	conditions TEXT NOT NULL, 
	severity VARCHAR(20) NOT NULL, 
	notify_channels TEXT NOT NULL, 
	cron_expression VARCHAR(50) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE analysis_result (
	task_id VARCHAR(36) NOT NULL, 
	result_type VARCHAR(30) NOT NULL, 
	content TEXT NOT NULL, 
	severity VARCHAR(20) NOT NULL, 
	confidence_score FLOAT NOT NULL, 
	structured_data TEXT NOT NULL, 
	source_log_refs TEXT NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE business_line (
	tenant_id VARCHAR(36) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	description TEXT NOT NULL, 
	es_index_pattern VARCHAR(500) NOT NULL, 
	log_parse_config TEXT NOT NULL, 
	default_filters TEXT NOT NULL, 
	severity_threshold VARCHAR(20) NOT NULL, 
	language VARCHAR(20) NOT NULL DEFAULT 'java', 
	field_mapping TEXT NOT NULL DEFAULT '{}', 
	ai_enabled BOOLEAN NOT NULL DEFAULT TRUE, 
	webhook_url VARCHAR(500) NOT NULL DEFAULT '', 
	is_active BOOLEAN NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE kb_document (
	kb_id VARCHAR(36) NOT NULL, 
	filename VARCHAR(500) NOT NULL, 
	file_path VARCHAR(1000) NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	chunk_count INTEGER NOT NULL, 
	metadata_json TEXT NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE knowledge_base (
	name VARCHAR(100) NOT NULL, 
	description TEXT NOT NULL, 
	embedding_provider_id VARCHAR(36), 
	vector_index_name VARCHAR(200) NOT NULL, 
	chunk_size INTEGER NOT NULL, 
	chunk_overlap INTEGER NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE log_analysis_task (
	business_line_id VARCHAR(36) NOT NULL, 
	provider_config_id VARCHAR(36), 
	prompt_template_id VARCHAR(36), 
	task_type VARCHAR(20) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	query_params TEXT NOT NULL, 
	time_from TIMESTAMP WITH TIME ZONE, 
	time_to TIMESTAMP WITH TIME ZONE, 
	log_count INTEGER NOT NULL, 
	token_usage INTEGER NOT NULL, 
	cost_usd FLOAT NOT NULL, 
	error_message TEXT, 
	stage_metrics TEXT NOT NULL DEFAULT '[]',
	started_at TIMESTAMP WITH TIME ZONE, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE agent_tool_call (
	task_id VARCHAR(36) NOT NULL, 
	step INTEGER NOT NULL, 
	tool_name VARCHAR(100) NOT NULL, 
	arguments TEXT NOT NULL DEFAULT '{}', 
	result_preview TEXT NOT NULL DEFAULT '', 
	result_length INTEGER NOT NULL DEFAULT 0, 
	duration_ms INTEGER NOT NULL DEFAULT 0, 
	success BOOLEAN NOT NULL DEFAULT TRUE, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id),
	FOREIGN KEY(task_id) REFERENCES log_analysis_task (id)
);

CREATE INDEX ix_agent_tool_call_task_id ON agent_tool_call (task_id);

CREATE TABLE prompt_template (
	name VARCHAR(100) NOT NULL, 
	category VARCHAR(50) NOT NULL, 
	version VARCHAR(20) NOT NULL, 
	description TEXT NOT NULL, 
	system_prompt TEXT NOT NULL, 
	user_prompt_template TEXT NOT NULL, 
	variables_schema TEXT NOT NULL, 
	metadata TEXT NOT NULL, 
	is_default BOOLEAN NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE provider_config (
	provider_type VARCHAR(20) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	api_base_url VARCHAR(500) NOT NULL, 
	api_key_encrypted TEXT NOT NULL, 
	default_model VARCHAR(100) NOT NULL, 
	model_params TEXT NOT NULL, 
	priority INTEGER NOT NULL, 
	rate_limit_rpm INTEGER NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE tenant (
	name VARCHAR(100) NOT NULL, 
	slug VARCHAR(50) NOT NULL, 
	description TEXT NOT NULL, 
	settings TEXT NOT NULL, 
	quota_tokens_daily INTEGER NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (slug)
);

CREATE TABLE "user" (
	tenant_id VARCHAR(36) NOT NULL, 
	username VARCHAR(50) NOT NULL, 
	email VARCHAR(200) NOT NULL, 
	hashed_password VARCHAR(200) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);

