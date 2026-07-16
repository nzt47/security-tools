{{/*
Common helpers for tlm-ops-reporter
*/}}

{{- define "tlm-ops-reporter.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tlm-ops-reporter.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "tlm-ops-reporter.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels
*/}}
{{- define "tlm-ops-reporter.labels" -}}
helm.sh/chart: {{ include "tlm-ops-reporter.chart" . }}
{{ include "tlm-ops-reporter.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "tlm-ops-reporter.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tlm-ops-reporter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
