/** 配置表单字段组件：label + 说明 + 后端校验错误回显的统一包装。 */

import { useId, type ReactNode } from "react";
import {
  Controller,
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

/** 按点路径从 RHF 嵌套对象（errors/dirtyFields）中取子节点。 */
function valueAtPath(source: unknown, path: string): unknown {
  let current: unknown = source;
  for (const part of path.split(".")) {
    if (current === null || typeof current !== "object") {
      return undefined;
    }
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

/** 按点路径从 RHF errors 嵌套对象中取错误消息。 */
function errorAtPath(errors: unknown, path: string): string | undefined {
  const current = valueAtPath(errors, path);
  if (current && typeof current === "object" && "message" in current) {
    const message = (current as { message?: unknown }).message;
    return typeof message === "string" ? message : undefined;
  }
  return undefined;
}

interface FieldShellProps {
  path: string;
  label: ReactNode;
  description?: string;
  controlId?: string;
  labelId?: string;
  children: ReactNode;
}

/** 字段外壳：标签、说明文字、校验错误、修改标记。 */
export function FieldShell({
  path,
  label,
  description,
  controlId,
  labelId,
  children,
}: FieldShellProps) {
  const {
    formState: { errors, dirtyFields },
  } = useFormContext();
  const error = errorAtPath(errors, path);
  const dirty = valueAtPath(dirtyFields, path) !== undefined;
  return (
    <div className="space-y-1" data-field-path={path}>
      <Label
        id={labelId}
        htmlFor={controlId}
        className="gap-1.5 font-mono text-xs font-medium tracking-wide"
      >
        {dirty ? (
          <span
            className="inline-block size-1.5 shrink-0 bg-accent-foreground/80"
            title="已修改"
            aria-hidden
          />
        ) : null}
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
export function TextField({
  path,
  label,
  description,
  placeholder,
  list,
}: BaseFieldProps & { list?: string }) {
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
        list={list}
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

/** 数字输入字段；清空时省略该键，非法中间态保留原文交由后端校验报可读错误。 */
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
          setValueAs: (value: unknown) => {
            if (value === "" || value === null || value === undefined) {
              return undefined;
            }
            const numeric = Number(value);
            // "1e" 等中间态 Number 得 NaN，序列化会变 null 导致 422 不知所云；
            // 保留原始字符串，让后端报"应为数字"并回显到字段。
            return Number.isFinite(numeric) ? numeric : value;
          },
        })}
      />
    </FieldShell>
  );
}

/** 开关字段。 */
export function SwitchField({
  path,
  label,
  description,
  onCheckedChange,
  disabled,
}: BaseFieldProps & {
  onCheckedChange?: (checked: boolean) => void;
  disabled?: boolean;
}) {
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
              checked={field.value === true}
              disabled={disabled}
              onCheckedChange={(checked) => {
                field.onChange(checked);
                onCheckedChange?.(checked);
              }}
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
  onValueChange,
}: BaseFieldProps & {
  options: SelectOption[];
  onValueChange?: (value: string) => void;
}) {
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
            onValueChange={(value) => {
              if (value === null) return;
              field.onChange(value);
              onValueChange?.(value);
            }}
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

/** 原始值数组受控编辑器，保留合法的 0 与正在输入的空行。 */
function ListField({
  path, label, description, placeholder, addLabel, numeric,
}: BaseFieldProps & { addLabel: string; numeric: boolean }) {
  const { control } = useFormContext();
  const labelId = useId();
  return (
    <FieldShell path={path} label={label} description={description} labelId={labelId}>
      <Controller
        control={control}
        name={path}
        render={({ field }) => {
          const values = (field.value ?? []) as (string | number)[];
          return (
            <div className="space-y-2" role="group" aria-labelledby={labelId}>
              {values.map((value, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Input
                    type={numeric ? "number" : "text"}
                    step={numeric ? "any" : undefined}
                    aria-label={`${label} ${index + 1}`}
                    placeholder={placeholder}
                    value={value}
                    onBlur={field.onBlur}
                    onChange={(event) => {
                      const text = event.target.value;
                      const next = [...values];
                      next[index] = numeric && text !== "" && Number.isFinite(Number(text))
                        ? Number(text) : text;
                      field.onChange(next);
                    }}
                  />
                  <Button
                    type="button" variant="ghost" size="icon"
                    aria-label={`删除${label}第 ${index + 1} 项`}
                    onClick={() => field.onChange(values.filter((_, i) => i !== index))}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
              <Button
                type="button" variant="outline" size="sm"
                onClick={() => field.onChange([...values, numeric ? 0 : ""])}
              >
                <Plus className="mr-1 h-4 w-4" />{addLabel}
              </Button>
            </div>
          );
        }}
      />
    </FieldShell>
  );
}

export function StringListField({ addLabel = "添加一行", ...props }: BaseFieldProps & { addLabel?: string }) {
  return <ListField {...props} addLabel={addLabel} numeric={false} />;
}

export function NumberListField({ addLabel = "添加一项", ...props }: BaseFieldProps & { addLabel?: string }) {
  return <ListField {...props} addLabel={addLabel} numeric />;
}
