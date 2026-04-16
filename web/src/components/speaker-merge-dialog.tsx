import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"

export interface SpeakerMergeInfo {
  oldName: string
  newName: string
  existingPersonId: string
  existingPersonName: string
  existingSampleCount: number
}

interface SpeakerMergeDialogProps {
  info: SpeakerMergeInfo | null
  onResolve: (choice: "merge" | "new" | "cancel") => void
}

export function SpeakerMergeDialog({ info, onResolve }: SpeakerMergeDialogProps) {
  const open = info !== null
  return (
    <AlertDialog
      open={open}
      onOpenChange={(v) => {
        if (!v) onResolve("cancel")
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>「{info?.newName}」已存在于声纹库</AlertDialogTitle>
          <AlertDialogDescription>
            声纹库中已有一位名为「{info?.existingPersonName}」的说话人
            （已收录 {info?.existingSampleCount ?? 0} 条声纹样本）。
            <br />
            <br />
            选择<strong>合并</strong>：把当前说话人的声纹样本归入已存在的「{info?.existingPersonName}」，合并后不可撤销。
            <br />
            选择<strong>新建为独立身份</strong>：保留为另一个声纹条目，名称会自动加上后缀以区分。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={() => onResolve("cancel")}>取消</AlertDialogCancel>
          <AlertDialogAction variant="secondary" onClick={() => onResolve("new")}>
            新建为独立身份
          </AlertDialogAction>
          <AlertDialogAction onClick={() => onResolve("merge")}>
            合并到已有身份
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
