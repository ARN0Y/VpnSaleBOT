import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";

export function Pager({
  page,
  hasMore,
  onPage,
  loading,
}: {
  page: number;
  hasMore: boolean;
  onPage: (p: number) => void;
  loading?: boolean;
}) {
  return (
    <div className="mt-4 flex items-center justify-between text-sm">
      <span className="text-muted-foreground">صفحه {page}</span>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" disabled={page <= 1 || loading} onClick={() => onPage(page - 1)}>
          <ChevronRight className="h-4 w-4" /> قبلی
        </Button>
        <Button size="sm" variant="outline" disabled={!hasMore || loading} onClick={() => onPage(page + 1)}>
          بعدی <ChevronLeft className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
