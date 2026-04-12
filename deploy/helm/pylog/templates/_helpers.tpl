{{/*
Common labels
*/}}
{{- define "logmind.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "logmind.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Full name
*/}}
{{- define "logmind.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Database URL
*/}}
{{- define "logmind.databaseUrl" -}}
postgresql+asyncpg://{{ .Values.config.database.username }}:{{ .Values.secrets.databasePassword }}@{{ .Values.config.database.host }}:{{ .Values.config.database.port }}/{{ .Values.config.database.name }}
{{- end }}

{{/*
Redis URL
*/}}
{{- define "logmind.redisUrl" -}}
redis://{{ .Values.config.redis.host }}:{{ .Values.config.redis.port }}/{{ .Values.config.redis.db }}
{{- end }}

{{/*
Celery Broker URL
*/}}
{{- define "logmind.celeryBrokerUrl" -}}
redis://{{ .Values.config.redis.host }}:{{ .Values.config.redis.port }}/{{ .Values.config.celery.brokerDb }}
{{- end }}
