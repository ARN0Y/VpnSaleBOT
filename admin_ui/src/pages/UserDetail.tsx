import * as React from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowRight, LayoutDashboard, Boxes, CreditCard, ShoppingCart, Crown, ShieldAlert, UserX, Gauge, Download } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { useUserDetail, useUserMutations, s } from "./userdetail/helpers";
import { UserHero } from "./userdetail/UserHero";
import { KpiRow } from "./userdetail/KpiRow";
import { OverviewTab } from "./userdetail/OverviewTab";
import { SubscriptionsTab } from "./userdetail/SubscriptionsTab";
import { TrafficTab } from "./userdetail/TrafficTab";
import { AccountTab } from "./userdetail/AccountTab";
import { OrdersTab } from "./userdetail/OrdersTab";
import { AgentTab } from "./userdetail/AgentTab";
import { DangerTab } from "./userdetail/DangerTab";

const TABS = [
  { value: "overview", label: "نمای کلی", icon: LayoutDashboard },
  { value: "subscriptions", label: "کانفیگ‌ها", icon: Boxes },
  { value: "traffic", label: "ترافیک", icon: Gauge },
  { value: "account", label: "کیف پول و پیام", icon: CreditCard },
  { value: "orders", label: "سفارش‌ها", icon: ShoppingCart },
  { value: "agent", label: "نمایندگی", icon: Crown },
  { value: "danger", label: "خطر", icon: ShieldAlert },
];

export function UserDetail() {
  const { userId } = useParams();
  const id = Number(userId);

  const [tab, setTab] = React.useState("overview");
  const [period, setPeriod] = React.useState("all");
  const [topupPeriod, setTopupPeriod] = React.useState("all");
  const [subsPage, setSubsPage] = React.useState(1);

  const { data, isLoading, isError } = useUserDetail(id, period, topupPeriod, subsPage);
  const mutations = useUserMutations(id);

  const back = (
    <Link to="/users" className="inline-flex items-center gap-2 text-sm text-muted-foreground transition hover:text-white">
      <ArrowRight className="h-4 w-4" /> بازگشت به کاربران
    </Link>
  );

  if (isLoading && !data) {
    return (
      <div className="space-y-5">
        {back}
        <Skeleton className="h-36 rounded-3xl" />
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
        </div>
        <Skeleton className="h-80" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="space-y-5">
        {back}
        <EmptyState icon={UserX} title="کاربر یافت نشد" hint="این کاربر وجود ندارد یا حذف شده است." />
      </div>
    );
  }

  const onJump = (target: string) => setTab(target === "wallet" || target === "message" ? "account" : target);

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `user-${s(data.user.user_id)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        {back}
        <Button size="sm" variant="ghost" onClick={exportJson}>
          <Download className="h-4 w-4" /> خروجی JSON
        </Button>
      </div>
      <UserHero user={data.user} mutations={mutations} onJump={onJump} />
      <KpiRow data={data} />

      <Tabs value={tab} onValueChange={setTab} className="space-y-5">
        <TabsList className="flex w-full flex-wrap gap-1">
          {TABS.map((t) => (
            <TabsTrigger key={t.value} value={t.value} className="flex items-center gap-1.5">
              <t.icon className="h-4 w-4" /> {t.label}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="overview"><OverviewTab data={data} /></TabsContent>
        <TabsContent value="subscriptions">
          <SubscriptionsTab data={data} mutations={mutations} subsPage={subsPage} setSubsPage={setSubsPage} />
        </TabsContent>
        <TabsContent value="traffic"><TrafficTab data={data} /></TabsContent>
        <TabsContent value="account">
          <AccountTab data={data} mutations={mutations} topupPeriod={topupPeriod} setTopupPeriod={setTopupPeriod} />
        </TabsContent>
        <TabsContent value="orders">
          <OrdersTab data={data} period={period} setPeriod={setPeriod} />
        </TabsContent>
        <TabsContent value="agent"><AgentTab data={data} mutations={mutations} /></TabsContent>
        <TabsContent value="danger"><DangerTab data={data} mutations={mutations} /></TabsContent>
      </Tabs>
    </div>
  );
}
