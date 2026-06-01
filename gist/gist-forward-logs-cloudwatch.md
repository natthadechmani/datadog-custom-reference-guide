# Forward ECS Logs from CloudWatch to Datadog

> Manual setup guide for forwarding ECS container logs to Datadog via CloudWatch Lambda triggers.
>
> The `quotes-api`, `generate-posts-api`, and `referrals-service` ECS services send container logs
> to the `ecs` CloudWatch log group prefix. This guide wires up the `DatadogIntegration-ForwarderStack`
> Lambda to forward those logs to Datadog.
>
> **Alternative:** You can also collect ECS logs directly with the Datadog Agent.
> See the [docs](https://docs.datadoghq.com/containers/amazon_ecs/logs) for that approach.

---

## Overview

CloudWatch captures ECS container stdout/stderr into log groups prefixed with `/ecs/`.  
A Lambda subscription trigger on each log group invokes the Datadog Forwarder, which ships
compressed log batches to the Datadog Logs intake over HTTPS.

```
 ECS Services                CloudWatch Log Groups           Lambda Forwarder           Datadog
┌────────────────┐          ┌──────────────────────┐        ┌──────────────────┐      ┌─────────┐
│ quotes-api     │──logs──▶ │ /ecs/…-quotes-api    │──────▶ │                  │      │         │
│ generate-posts │──logs──▶ │ /ecs/…-generate-posts│──────▶ │ ForwarderStack   │─────▶│  Logs   │
│ referrals-svc  │──logs──▶ │ /ecs/…-referrals-svc │──────▶ │                  │      │         │
└────────────────┘          └──────────────────────┘        └──────────────────┘      └─────────┘
```

---

## Steps

### 1 — Locate the ECS log groups

1. In the AWS console, navigate to **CloudWatch > Logs > Log groups**.
2. Find the three log groups prefixed with `ecs`.

    [![ECS log groups in CloudWatch](https://play.instruqt.com/assets/tracks/5oluhqoofsnj/51d0193ec5f612e9c55274135c62d3ea/assets/ecs_log_groups.png)](https://play.instruqt.com/assets/tracks/5oluhqoofsnj/51d0193ec5f612e9c55274135c62d3ea/assets/ecs_log_groups.png)

3. Open any log group and explore some entries to confirm logs are flowing.

> **Note:** `generate-posts-api` and `referrals-service` generate verbose logs.
> `quotes-api` may have fewer entries.

---

### 2 — Open the Datadog Forwarder Lambda

1. Navigate to **Lambda > Functions**.
2. Search for the forwarder function by name:

    ```
    DatadogIntegration-ForwarderStack
    ```

    [![Find the log-forwarder function](https://play.instruqt.com/assets/tracks/5oluhqoofsnj/c943ca3d1bbddbe70de255704374f48d/assets/log_forwarder_function.png)](https://play.instruqt.com/assets/tracks/5oluhqoofsnj/c943ca3d1bbddbe70de255704374f48d/assets/log_forwarder_function.png)

3. Click the function to open it.

---

### 3 — Add a CloudWatch Logs trigger for `generate-posts-api`

1. Click **Add trigger** on the left side of the function page.

    [![Add trigger to Lambda function](https://play.instruqt.com/assets/tracks/5oluhqoofsnj/50def59c793ae2479b76403ffdd9c016/assets/add_function_trigger.png)](https://play.instruqt.com/assets/tracks/5oluhqoofsnj/50def59c793ae2479b76403ffdd9c016/assets/add_function_trigger.png)

2. Under **Select a source**, type `cloudwatch` and select **CloudWatch Logs**.
3. Under **Log group**, search for `ecs` and select the ARN for the `generate-posts-api` log group.
   The ARN looks like:

    ```
    arn:aws:logs:us-east-1:026090514899:log-group:/ecs/TechStoriesStack-ECSServicesStack-[random string]-generate-posts-api:*
    ```

4. Under **Filter name**, enter:

    ```
    ecs-cloudwatch-logs-generate-posts-api
    ```

    Confirm your trigger configuration looks like this:

    [![Log group trigger configuration](https://play.instruqt.com/assets/tracks/5oluhqoofsnj/a7f8600289d69b83427c5717d9605c1b/assets/cloudwatch_trigger_configuration.png)](https://play.instruqt.com/assets/tracks/5oluhqoofsnj/a7f8600289d69b83427c5717d9605c1b/assets/cloudwatch_trigger_configuration.png)

5. Click **Add**. You should see the **CloudWatch Logs** trigger attached to the Forwarder Lambda.

    [![Forwarder Lambda with CloudWatch Logs trigger](https://play.instruqt.com/assets/tracks/5oluhqoofsnj/92d4f20ea7653983a45dcca69e944e89/assets/successful_cloudwatch_trigger.png)](https://play.instruqt.com/assets/tracks/5oluhqoofsnj/92d4f20ea7653983a45dcca69e944e89/assets/successful_cloudwatch_trigger.png)

---

### 4 — Repeat for `quotes-api` and `referrals-service`

Follow the same steps in [Step 3](#3--add-a-cloudwatch-logs-trigger-for-generate-posts-api)
for the remaining two log groups, using the filter names below.

**`quotes-api`:**

```
ecs-cloudwatch-logs-quotes-api
```

**`referrals-service`:**

```
ecs-cloudwatch-logs-referrals-api
```

---

### 5 — Verify all three triggers are active

1. On the Lambda function page, click the **Configuration** tab.
2. Select **Triggers**.
3. Confirm three **CloudWatch Logs** triggers appear — one for each ECS log group.

    [![Lambda triggers configuration with three CloudWatch Logs triggers](https://play.instruqt.com/assets/tracks/5oluhqoofsnj/7fe9dfcce78c02eb0ec19364ffe2994e/assets/lambda_triggers_config.png)](https://play.instruqt.com/assets/tracks/5oluhqoofsnj/7fe9dfcce78c02eb0ec19364ffe2994e/assets/lambda_triggers_config.png)

---

## Verify logs in Datadog

Open **Logs > Live Tail** and filter by source or service tag to confirm ECS logs are arriving.

Example query:

```
source:cloudwatch service:generate-posts-api
```

---

## References

- [Datadog Forwarder for AWS](https://docs.datadoghq.com/logs/guide/forwarder/)
- [ECS Log Collection with Datadog Agent](https://docs.datadoghq.com/containers/amazon_ecs/logs)
- [CloudWatch Log Groups console](https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#logsV2:log-groups)
- [Lambda Functions console](https://us-east-1.console.aws.amazon.com/lambda)
