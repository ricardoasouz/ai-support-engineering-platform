{{- define "ai-support-platform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "ai-support-platform.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "ai-support-platform.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "ai-support-platform.labels" -}}
helm.sh/chart: {{ include "ai-support-platform.chart" . }}
app.kubernetes.io/name: {{ include "ai-support-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "ai-support-platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ai-support-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "ai-support-platform.componentLabels" -}}
{{ include "ai-support-platform.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "ai-support-platform.secretName" -}}
{{- default (printf "%s-secrets" (include "ai-support-platform.fullname" .)) .Values.secrets.existingSecret }}
{{- end }}

{{- define "ai-support-platform.apiServiceAccountName" -}}
{{- default (printf "%s-api" (include "ai-support-platform.fullname" .)) .Values.serviceAccounts.apiName }}
{{- end }}

{{- define "ai-support-platform.workerServiceAccountName" -}}
{{- default (printf "%s-worker" (include "ai-support-platform.fullname" .)) .Values.serviceAccounts.workerName }}
{{- end }}

{{- define "ai-support-platform.jobsServiceAccountName" -}}
{{- default (printf "%s-jobs" (include "ai-support-platform.fullname" .)) .Values.serviceAccounts.jobsName }}
{{- end }}

{{- define "ai-support-platform.platformServiceAccountName" -}}
{{- default (printf "%s-platform" (include "ai-support-platform.fullname" .)) .Values.serviceAccounts.platformName }}
{{- end }}

{{- define "ai-support-platform.validateValues" -}}
{{- if and .Values.secrets.create .Values.secrets.existingSecret }}
{{- fail "secrets.create and secrets.existingSecret are mutually exclusive" }}
{{- end }}
{{- if and (not .Values.secrets.create) (not .Values.secrets.existingSecret) }}
{{- fail "set secrets.existingSecret or enable secrets.create with explicit secret values" }}
{{- end }}
{{- if eq (toString .Values.image.tag) "latest" }}
{{- fail "image.tag must be immutable or development-specific; latest is not allowed" }}
{{- end }}
{{- if and (not .Values.kafka.enabled) (not .Values.kafka.externalBootstrapServers) }}
{{- fail "kafka.externalBootstrapServers is required when kafka.enabled=false" }}
{{- end }}
{{- if and (not .Values.ollama.enabled) (eq .Values.config.providers.llm "ollama") (not .Values.ollama.externalBaseUrl) }}
{{- fail "ollama.externalBaseUrl is required for the Ollama provider when ollama.enabled=false" }}
{{- end }}
{{- if and .Values.networkPolicy.enabled (or (not .Values.postgres.enabled) (not .Values.kafka.enabled)) (eq (len .Values.networkPolicy.externalEgressCidrs) 0) }}
{{- fail "networkPolicy.externalEgressCidrs is required when external PostgreSQL or Kafka is used with NetworkPolicy enabled" }}
{{- end }}
{{- if and .Values.config.providers.allowFake (not (or (eq .Values.global.appEnvironment "test") (eq .Values.global.appEnvironment "integration"))) }}
{{- fail "fake providers are allowed only in test or integration environments" }}
{{- end }}
{{- end }}
