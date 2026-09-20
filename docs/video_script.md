# Demo video script (target 2:50, hard limit 3:00)

Record your screen with your voice. Upload to YouTube as **public or unlisted** and open the
link in a private window before you submit. Say the words in your own style.

**0:00 - 0:20  The problem** (dashboard on screen)
"Small merchants find out about fraud days later, from a chargeback. SpikeGuard watches every
merchant's payments in five-minute windows and tells a human the moment traffic stops looking
normal. It never blocks a payment. A person decides."

**0:20 - 0:45  What you are looking at**
Point at the headline, the pulse trace, the review queue. "Each point on this line is one
five-minute window across six merchants. The red spikes are flagged windows. This is running
live on AWS, and every number comes from DynamoDB."

**0:45 - 1:35  Demo 1: card testing, end to end**
Pick Pink City Handlooms, click **Card testing**. Show the result box, then the new alert.
Read the reasons out loud (declines, tiny amounts, many card BINs from few devices). Point at
the suggested next step. Click **Confirm fraud** and show the precision number change.

**1:35 - 2:00  Demo 2: what it should NOT flag**
Click **Flash sale**. "Volume is three times normal, but the payment mix is normal, so it stays
quiet. That is the hard part: telling a busy day from an attack." Then click **Bust-out** or
**Geographic anomaly** for a quick second catch.

**2:00 - 2:25  The AWS side** (switch to the AWS console, about 5 seconds each)
CloudFormation stack (one template) -> DynamoDB items -> Lambda -> CloudWatch metrics
(`SpikeGuard` namespace) -> the SNS alert email in your inbox.

**2:25 - 2:50  Honesty and learning**
"The traffic is synthetic. My subtle-attack recall is 90 percent, and geographic anomalies are
the weak spot at 71 percent. A new merchant has a learning period. Here is what I learned this
weekend: [your real answer]."

**2:50 - 3:00  Close**
"SpikeGuard: catch the spike, explain it, let a human decide."
