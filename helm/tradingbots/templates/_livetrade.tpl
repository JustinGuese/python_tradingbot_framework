{{/*
Shared pieces of the livetrade CronJobs.

The six livetrade templates were ~604 lines of which roughly 400 were identical:
the same CronJob skeleton, the same Postgres/secret env plumbing, and the same
five LIVETRADE_* config vars. Only the credential block genuinely differs per
broker (and IBKR additionally mounts OAuth key files), so that is all each
template still spells out.

Deliberately three helpers rather than one: IBKR needs to inject volumeMounts
and volumes into the middle of the pod spec, which a single monolithic template
could not express without a passthrough parameter for every possible insertion
point.

`helm template` output is byte-identical to the pre-refactor templates.
*/}}

{{/*
CronJob skeleton, ending with `env:` so the caller appends env entries at 12 spaces.
Args: root (the $ context), name, schedule, module.
*/}}
{{- define "tradingbots.livetrade.header" -}}
{{- $ := .root -}}
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{ .name }}
  namespace: {{ $.Values.namespace }}
  labels:
    app.kubernetes.io/managed-by: Helm
  annotations:
    meta.helm.sh/release-name: {{ $.Release.Name }}
    meta.helm.sh/release-namespace: {{ $.Release.Namespace }}
spec:
  schedule: {{ .schedule | quote }}
  successfulJobsHistoryLimit: {{ $.Values.cronjob.successfulJobsHistoryLimit }}
  failedJobsHistoryLimit: {{ $.Values.cronjob.failedJobsHistoryLimit }}
  concurrencyPolicy: {{ $.Values.cronjob.concurrencyPolicy }}
  jobTemplate:
    spec:
      activeDeadlineSeconds: {{ $.Values.cronjob.activeDeadlineSeconds }}
      ttlSecondsAfterFinished: {{ $.Values.cronjob.ttlSecondsAfterFinished }}
      template:
        spec:
          imagePullSecrets:
          {{- range $.Values.imagePullSecrets }}
          - name: {{ .name }}
          {{- end }}
          containers:
          - name: {{ .name }}
            image: {{ $.Values.image.repository }}:{{ $.Values.image.tag }}
            command:
            - "python"
            args:
            - "-m"
            - {{ .module | quote }}
            imagePullPolicy: {{ $.Values.image.pullPolicy }}
            resources:
              {{- toYaml $.Values.resources | nindent 14 }}
            env:
{{- end -}}

{{/*
Postgres connection env plus the shared .Values.env list.
Args: root.
*/}}
{{- define "tradingbots.livetrade.commonEnv" -}}
{{- $ := .root -}}
{{- if $.Values.postgresql.clusterConnection }}
            - name: POSTGRES_HOST
              value: {{ $.Values.postgresql.clusterConnection.host | quote }}
            - name: POSTGRES_PORT
              value: {{ $.Values.postgresql.clusterConnection.port | quote }}
            - name: POSTGRES_USER
              value: {{ $.Values.postgresql.clusterConnection.user | quote }}
            - name: POSTGRES_DATABASE
              value: {{ $.Values.postgresql.clusterConnection.database | quote }}
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: {{ $.Values.secretName }}
                  key: POSTGRES_PASSWORD
            {{- end }}
            {{- range $.Values.env }}
            {{- if and $.Values.postgresql.clusterConnection (or (eq .name "POSTGRES_URI") (eq .name "POSTGRES_PASSWORD")) }}
            {{- else }}
            - name: {{ .name }}
            {{- if .value }}
              value: {{ .value | quote }}
            {{- else if .valueFrom }}
              valueFrom:
                secretKeyRef:
                  name: {{ $.Values.secretName }}
                  key: {{ .valueFrom.secretKeyRef.key }}
            {{- end }}
            {{- end }}
            {{- end }}
{{- end -}}

{{/*
The five LIVETRADE_* vars. Each falls back to the chart-wide default unless the
instance overrides it, which is what lets two Collective2 copiers run different
bot weights against different strategy ids.
Args: root, cfg (the per-instance .Values.liveTrade.<instance> map).
*/}}
{{- define "tradingbots.livetrade.configEnv" -}}
{{- $ := .root -}}
{{- $cfg := .cfg }}
            # LiveTrade configuration (from values.yaml)
            - name: LIVETRADE_BOT_WEIGHTS
              value: {{ default $.Values.liveTrade.botWeights $cfg.botWeights | quote }}
            - name: LIVETRADE_MIN_ORDER_USD
              value: {{ default $.Values.liveTrade.minOrderUsd $cfg.minOrderUsd | quote }}
            - name: LIVETRADE_DRY_RUN
              value: {{ default $.Values.liveTrade.dryRun $cfg.dryRun | quote }}
            - name: LIVETRADE_PORTFOLIO_FRACTION
              value: {{ default $.Values.liveTrade.portfolioFraction $cfg.portfolioFraction | quote }}
            - name: LIVETRADE_STRICT_MAPPING
              value: {{ $.Values.liveTrade.strictMapping | quote }}
{{- end -}}
