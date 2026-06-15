# APM SSI on AWS EKS Fargate

---

## Table of Contents

1. [Introduction](#introduction)
2. [Agent Sidecar Container](#agent-sidecar-container)
3. [Enabling APM SSI on EKS Fargate](#enabling-apm-ssi-on-eks-fargate)
  - [Step 1: Create the Datadog secret](#step-1-create-the-datadog-secret)
  - [Step 2: Deploy the Cluster Agent](#step-2-deploy-the-cluster-agent)
  - [Step 3: Create RBAC, ServiceAccount, and ClusterRoleBinding](#step-3-create-the-necessary-rbacs-service-account-and-clusterrolebinding)
  - [Step 4: Deploy your App](#step-4-deploy-your-app)
  - [Step 5: Wait for pods to start](#step-5-wait-for-the-pods-to-start)
  - [Step 6: Language detection annotates the deployment](#step-6-language-detection-will-automatically-annotate-the-deployment)
  - [Step 7: Scale up the deployment](#step-7-scaling-up-the-deployment)
4. [FAQ](#faq)

---

## Introduction

In most common Kubernetes scenarios, the Datadog Agent is deployed as a **DaemonSet** on the user cluster — one agent pod per node, alongside a Cluster Agent running as a Deployment.

When running on **AWS EKS Fargate**, things work differently due to its serverless nature. EKS Fargate has no concept of nodes; each pod appears to run on its own node and the underlying infrastructure is fully abstracted away. For this reason, the agent **cannot** be deployed as a DaemonSet on EKS Fargate. Instead, it is deployed as a **sidecar container** alongside each applicative pod.

This document explains how this works and how to configure it.

---

## Architecture - [Agent Sidecar Container]

```
AWS EKS Fargate Cluster
│
├── Datadog Cluster Agent (Deployment)
│   ├── Admission Controller  ──► intercepts pods with label agent.datadoghq.com/sidecar: fargate
│   └── Language Detection    ──► annotates Deployments with detected languages
│
└── Fargate Pod (per app)
    ├── App Container          (user workload)
    ├── DD Agent Sidecar       (auto-injected)  ◄──► Cluster Agent
    └── Init Container(s)      (tracing libs injected by Admission Controller)
         ├── Before language detection: all 6 libs (java, python, node, dotnet, ruby, php)
         └── After language detection: only detected language (e.g. python only)

DD Agent Sidecar ──► Datadog SaaS (metrics, traces, logs)
```

On EKS Fargate, user applications run as pods. A pod is defined by a template describing the containers, memory and CPU constraints, labels, annotations, and so on.

A minimal pod spec looks like this:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: nginx
spec:
  containers:
  - name: nginx
    image: nginx:1.14.2
    ports:
    - containerPort: 80
```

To monitor this pod on EKS Fargate, add the Datadog Agent as a second container in the same pod spec:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: nginx
spec:
  containers:
  - name: nginx
    image: nginx:1.14.2
    ports:
    - containerPort: 80

  # Running the Agent as a sidecar
  - image: datadog/agent
    name: datadog-agent
    env:
    - name: DD_API_KEY
      value: "<YOUR_DATADOG_API_KEY>"
    # Set DD_SITE to "datadoghq.eu" to send data to the Datadog EU site
    - name: DD_SITE
      value: "datadoghq.com"
    - name: DD_EKS_FARGATE
      value: "true"
    - name: DD_CLUSTER_NAME
      value: "<CLUSTER_NAME>"
    - name: DD_KUBERNETES_KUBELET_NODENAME
      valueFrom:
        fieldRef:
          apiVersion: v1
          fieldPath: spec.nodeName
    resources:
      requests:
        memory: "256Mi"
        cpu: "200m"
      limits:
        memory: "256Mi"
        cpu: "200m"
```

> **Note:** `DD_EKS_FARGATE` must be set to `"true"` for the agent to operate correctly in this mode. Refer to the [official documentation](https://docs.datadoghq.com/integrations/eks_fargate/) for the full setup requirements.

---

## Enabling APM SSI on EKS Fargate

There are two methods to run the agent on EKS Fargate:

1. **Manual agent sidecar installation**
2. **Automatic agent sidecar injection**

APM SSI (Single Step Instrumentation) is only supported with **[automatic agent sidecar injection](https://docs.datadoghq.com/integrations/eks_fargate/?tab=admissioncontroller)**.

The Admission Controller handles injection and configuration of the agent sidecar container automatically. Users can still customize the sidecar via Helm values.

The following is a working end-to-end example. All steps are performed in the `fargate` namespace.

---

### Step 1: Create the Datadog secret

** For mixed deployment with traditional setup & fargate pods please refer [here](https://docs.datadoghq.com/integrations/eks_fargate/?tab=admissioncontrollerhelm#secret-for-keys-and-tokens)

```bash
kubectl create secret generic datadog-secret \
  --from-literal api-key=<YOUR_API_KEY> \
  --from-literal token=<CLUSTER_AGENT_TOKEN> \
  -n fargate
```

** token is a 32-character alphanumeric token for the Cluster Agent & Datadog Agent to secure communication
  `--from-literal token=$(openssl rand -hex 16)`

---

### Step 2: Deploy the Cluster Agent

```bash
helm repo add datadog https://helm.datadoghq.com
helm repo update

helm install datadog-agent -f datadog-values.yaml datadog/datadog -n fargate
```

```yaml
datadog:
  apiKeyExistingSecret: datadog-secret
  clusterName: <CLUSTER_NAME>
  apm:
    enabled: true
    instrumentation:
      enabled: true
      targets:
        - name: "default_fargate_namespace"
          namespaceSelector:
            matchNames:
              - "fargate"

agents:
  enabled: false  # Node agent disabled — agent runs as sidecar on a Fargate-only cluster

clusterAgent:
  tokenExistingSecret: datadog-secret
  admissionController:
    agentSidecarInjection:
      enabled: true
      provider: fargate
```

This Helm chart configures the Cluster Agent with:

- Admission Controller and agent sidecar injection enabled for the `fargate` provider
- APM SSI enabled in the `fargate` namespace
- Language detection enabled on automatically injected agent sidecars
- Node agent disabled (not applicable on Fargate)

> **Note:** The Cluster Agent is enabled by default since Helm chart v2.7.0 — no need to set `clusterAgent.enabled: true` explicitly. The Helm chart also automatically creates the necessary RBAC for the Cluster Agent itself and generates a shared token between the Cluster Agent and agent sidecars.

---

### Step 3: Create the necessary RBACs, Service Account, and ClusterRoleBinding

The Admission Controller does not adjust the `serviceAccountName` of your pods — if the pod's ServiceAccount lacks the correct RBAC, the injected agent sidecar cannot connect to Kubernetes.

Run kubectl apply -f rbac.yaml

```bash
kubectl apply -f rbac.yaml
```

**Contents of `rbac.yaml`:**

```yaml
# Create a ClusterRole for the necessary permissions and bind it to the ServiceAccount your pods:
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: datadog-agent-fargate
rules:
  - apiGroups:
    - ""
    resources:
    - nodes
    - namespaces
    - endpoints
    verbs:
    - get
    - list
  - apiGroups:
      - ""
    resources:
      - nodes/metrics
      - nodes/spec
      - nodes/stats
      - nodes/proxy
      - nodes/pods
      - nodes/healthz
    verbs:
      - get
---
# Create a ClusterRoleBinding to attach this to the namespaced ServiceAccount that your pods are currently using. The ClusterRoleBindings below reference this previously created ClusterRole.
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: datadog-agent-fargate
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: datadog-agent-fargate
subjects:
  - kind: ServiceAccount
    name: datadog-agent 
    namespace: fargate 
---
# This creates a ServiceAccount named datadog-agent in the fargate namespace that is referenced in the ClusterRoleBinding. Adjust this for your Fargate pods’ namespace and set this as the serviceAccountName in your pod spec.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: datadog-agent
  namespace: fargate

```

for multi serviceAccountName across Namespaces please refer [here](https://docs.datadoghq.com/integrations/eks_fargate/?tab=admissioncontrollerhelm#if-you-are-using-multiple-serviceaccounts-across-namespaces)

---

### Step 4: Deploy your App

Test deploy dummy python app with:

```bash
kubectl apply -f app.yaml
```

Notice the labels that we are setting in the spec template metadata:


| Label                                     | Purpose                                                                                                                                               |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent.datadoghq.com/sidecar: "fargate"`  | Triggers automatic agent sidecar injection by the Admission Controller.                                                                               |


**Contents of `app.yaml`:**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: fargate # namespace with APM SSI enabled
spec:
  selector:
    matchLabels:
      app: main-app
  replicas: 2
  template:
    metadata:
      labels:
        app: main-app
        agent.datadoghq.com/sidecar: "fargate" # important to inject sidecar container
      name: my-app
    spec:
      serviceAccountName: datadog-agent
      containers:
        - name: python-container
          image: python:3.9-slim
          command: ["/bin/sh"]
          args:
            - "-c"
            - "python -c 'import time\nwhile True: time.sleep(4)' & python -c 'import time\nwhile True: time.sleep(4)' && wait"
```

---

### Step 5: Wait for the pods to start

The first pods of this deployment will receive **all 6 tracing library init containers**, since language detection results are not yet available at this point.

```
$ kubectl get pods
NAME                                           READY   STATUS    RESTARTS   AGE
datadog-agent-cluster-agent-c5dff5ccc-5qxq5   1/1     Running   0          26m
my-app-595c966dc5-lq7pn                        2/2     Running   0          24m
my-app-595c966dc5-qk9rs                        2/2     Running   0          24m
```

Inspecting one of these pods will show 6 init containers alongside the agent sidecar container.

---

### Step 6: Language detection will automatically annotate the deployment

After the pods start running, the Cluster Agent's language detection will automatically annotate the deployment:

```bash
kubectl get deployment my-app -o yaml
```

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  annotations:
    deployment.kubernetes.io/revision: "1"
    internal.dd.datadoghq.com/python-container.detected_langs: python
  ...
```

---

### Step 7: Scaling up the deployment

With language detection active (no pinned `ddTraceVersions`), the Cluster Agent uses the detected-language annotation from Step 6 to inject **only the relevant tracing library** into newly created pods.

```bash
kubectl scale deployment my-app --replicas=4 -n fargate
```

A newly created pod now has a single tracing-library init container instead of all six:

```bash
kubectl get pod <new-pod> -n fargate -o jsonpath='{.spec.initContainers[*].name}'
# datadog-lib-python-init
```

> **Note:**
> - Pods created **before** the annotation existed keep all six init containers until they are recreated (rollout restart or rescheduling).
> - The Datadog Agent sidecar is still injected into **every** pod via the `agent.datadoghq.com/sidecar: "fargate"` label.
> - This narrowing happens **only** when tracer versions are not pinned — pinning `ddTraceVersions` disables it (see [FAQ](#faq)).

---

## FAQ

**Q: Why do my first pods have six init containers but later ones have one?**

Language detection runs *after* the first pods start. The Cluster Agent detects the language, annotates the Deployment (`internal.dd.datadoghq.com/<container>.detected_langs`), and subsequent pods receive only that library. The first pods get all supported languages because no detection data exists yet.

**Q: Must the secret be named `datadog-secret`?**

Yes — for the injected sidecar. The Admission Controller hardcodes the name `datadog-secret` (keys `api-key` and `token`) and reads it from the **pod's own namespace**. It must exist, with both keys, in every namespace that runs instrumented Fargate pods. Helm's `apiKeyExistingSecret` / `tokenExistingSecret` values only control what the **Cluster Agent** reads — they do not rename the sidecar's secret.

**Q: Can I pin tracer library versions?**

Yes, via `ddTraceVersions` — but use the `v` prefix (`java: "v1"`) to match the published image tags, or the init container hits `ImagePullBackOff`. Also note that pinning any non-default version disables language-detection narrowing, so **all** listed libraries are injected into every pod.

**Q: My Cluster Agent pod won't start / can't find the secret.**

It reads `datadog-secret` from its own (release) namespace. Install the chart into the same namespace as the secret (`helm install ... -n fargate`), or also create the secret in the release namespace.

**Q: I also run EC2 node groups (mixed cluster).**

Set `agents.enabled: true` so the DaemonSet covers the EC2 nodes; the sidecar path covers the Fargate pods. `agents.enabled: false` is only appropriate for Fargate-only clusters.

---

## Reference
- [Amazon EKS on AWS Fargate](https://docs.datadoghq.com/integrations/eks_fargate/?tab=admissioncontrollerhelm#how-datadog-monitors-eks-fargate-pods)
- [Single Step Instrumentation on Kubernetes](https://docs.datadoghq.com/tracing/trace_collection/single-step-apm/kubernetes/?tab=agentv764recommended#overview)
- [Datadog Cluster Agent - Admission Controller](https://docs.datadoghq.com/containers/cluster_agent/admission_controller/?tab=helm)