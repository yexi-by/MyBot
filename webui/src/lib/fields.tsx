/** 配置表单字段组件：label + 说明 + 后端校验错误回显的统一包装。 */

import { useId, type ReactNode } from "react";
import {
  Controller,
  useFieldArray,
  useFormContext,
} from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Plus, Trash2 } from "lucide-react";

/** 按点路径从 RHF errors 嵌套对象中取错误消息。 */
function errorAtPath(errors: unknown, path: string): string | undefined {
  let current: unknown = errors;
  for (const part of path.split(".")) {
    if (current === null || typeof current !== "object") {
      return undefined;
    }
    current = (current as Record<string, unknown>)[part];
  }
  if (current && typeof current === "object" && "message" in current) {
    const message = (current as { message?: unknown }).message;
    return typeof message === "string" ? message : undefined;
  }
  return undefined;
}

interface FieldShellProps {
  path: string;
  label: string;
  description?: string;
  controlId?: string;
  labelId?: string;
  children: ReactNode;
}

/** 字段外壳：标签、说明文字、校验错误。 */
export function FieldShell({
  path,
  label,
  description,
  controlId,
  labelId,
  children,
}: FieldShellProps) {
  const {
    formState: { errors },
  } = useFormContext();
  const error = errorAtPath(errors, path);
  return (
    <div className="space-y-1.5">
      <Label id={labelId} htmlFor={controlId}>
        {label}
      </Label>
      {children}
      {description ? (
        <p className="text-xs text-muted-foreground">{description}</p>
      ) : null}
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}

interface BaseFieldProps {
  path: string;
  label: string;
  description?: string;
  placeholder?: string;
}

/** 文本输入字段；清空时从配置载荷中省略该键。 */
export function TextField({ path, label, description, placeholder }: BaseFieldProps) {
  const { register } = useFormContext();
  const controlId = useId();
  return (
    <FieldShell
      path={path}
      label={label}
      description={description}
      controlId={controlId}
    >
      <Input
        id={controlId}
        placeholder={placeholder}
        {...register(path, {
          setValueAs: (value: unknown) => value === "" ? undefined : value,
        })}
      />
    </FieldShell>
  );
}

/** 多行文本字段。 */
export function TextareaField({
  path,
  label,
  description,
  placeholder,
  rows = 4,
}: BaseFieldProps & { rows?: number }) {
  const { register } = useFormContext();
  const controlId = useId();
  return (
    <FieldShell
      path={path}
      label={label}
      description={description}
      controlId={controlId}
    >
      <Textarea
        id={controlId}
        rows={rows}
        placeholder={placeholder}
        {...register(path)}
      />
    </FieldShell>
  );
}

/** 数字输入字段；清空等价于恢复默认（提交时丢弃该键）。 */
export function NumberField({
  path,
  label,
  description,
  placeholder,
  step,
}: BaseFieldProps & { step?: number | string }) {
  const { register } = useFormContext();
  const controlId = useId();
  return (
    <FieldShell
      path={path}
      label={label}
      description={description}
      controlId={controlId}
    >
      <Input
        id={controlId}
        type="number"
        step={step ?? "any"}
        placeholder={placeholder}
        {...register(path, {
          setValueAs: (value: unknown) =>
            value === "" || value === null || value === undefined
              ? undefined
              : Number(value),
        })}
      />
    </FieldShell>
  );
}

/** 开关字段。 */
export function SwitchField({ path, label, description }: BaseFieldProps) {
  const { control } = useFormContext();
  const controlId = useId();
  return (
    <FieldShell
      path={path}
      label={label}
      description={description}
      controlId={controlId}
    >
      <div>
        <Controller
          control={control}
          name={path}
          render={({ field }) => (
            <Switch
              id={controlId}
              aria-label={label}
              checked={Boolean(field.value)}
              onCheckedChange={field.onChange}
            />
          )}
        />
      </div>
    </FieldShell>
  );
}

interface SelectOption {
  value: string;
  label: string;
}

/** 下拉选择字段。 */
export function SelectField({
  path,
  label,
  description,
  options,
  placeholder = "请选择",
}: BaseFieldProps & { options: SelectOption[] }) {
  const { control } = useFormContext();
  const controlId = useId();
  return (
    <FieldShell
      path={path}
      label={label}
      description={description}
      controlId={controlId}
    >
      <Controller
        control={control}
        name={path}
        render={({ field }) => (
          <Select
            value={typeof field.value === "string" ? field.value : ""}
            onValueChange={field.onChange}
          >
            <SelectTrigger id={controlId} className="w-full">
              <SelectValue placeholder={placeholder} />
            </SelectTrigger>
            <SelectContent>
              {options.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      />
    </FieldShell>
  );
}

/** 字符串数组行编辑器（群号、用户 ID 列表）。 */
export function StringListField({
  path,
  label,
  description,
  placeholder,
  addLabel = "添加一行",
}: BaseFieldProps & { addLabel?: string }) {
  const { control, register } = useFormContext();
  const { fields, append, remove } = useFieldArray({ control, name: path });
  const labelId = useId();
  return (
    <FieldShell
      path={path}
      label={label}
      description={description}
      labelId={labelId}
    >
      <div className="space-y-2" role="group" aria-labelledby={labelId}>
        {fields.map((field, index) => (
          <div key={field.id} className="flex items-center gap-2">
            <Input
              aria-label={`${label} ${index + 1}`}
              placeholder={placeholder}
              {...register(`${path}.${index}`)}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`删除${label}第 ${index + 1} 项`}
              onClick={() => remove(index)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => append("")}
        >
          <Plus className="mr-1 h-4 w-4" />
          {addLabel}
        </Button>
      </div>
    </FieldShell>
  );
}

/** 键值对编辑器（MCP server 的 env）。键不允许重复，后者覆盖前者。 */
export function KeyValueField({
  path,
  label,
  description,
}: BaseFieldProps) {
  const { watch, setValue } = useFormContext();
  const labelId = useId();
  const record = (watch(path) as Record<string, string> | undefined) ?? {};
  const entries = Object.entries(record);

  const commit = (next: [string, string][]) => {
    setValue(path, Object.fromEntries(next), { shouldDirty: true });
  };

  return (
    <FieldShell
      path={path}
      label={label}
      description={description}
      labelId={labelId}
    >
      <div className="space-y-2" role="group" aria-labelledby={labelId}>
        {entries.map(([key, value], index) => (
          <div key={index} className="flex items-center gap-2">
            <Input
              className="w-2/5"
              aria-label={`变量名 ${index + 1}`}
              placeholder="变量名"
              value={key}
              onChange={(event) => {
                const next: [string, string][] = entries.map((entry, i) =>
                  i === index ? [event.target.value, entry[1]] : entry,
                );
                commit(next);
              }}
            />
            <Input
              aria-label={`变量值 ${index + 1}`}
              placeholder="值"
              value={value}
              onChange={(event) => {
                const next: [string, string][] = entries.map((entry, i) =>
                  i === index ? [entry[0], event.target.value] : entry,
                );
                commit(next);
              }}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`删除环境变量 ${key || index + 1}`}
              onClick={() => commit(entries.filter((_, i) => i !== index))}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => commit([...entries, ["", ""]])}
        >
          <Plus className="mr-1 h-4 w-4" />
          添加变量
        </Button>
      </div>
    </FieldShell>
  );
}
