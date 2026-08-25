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

/** 数字输入字段；清空时省略该键，必填字段会由后端明确报错。 */
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

/** 数字数组编辑器；空数组表示当前功能不执行对应的重复操作。 */
export function NumberListField({
  path,
  label,
  description,
  addLabel = "添加一项",
}: BaseFieldProps & { addLabel?: string }) {
  const { control, register } = useFormContext();
  const { fields, append, remove } = useFieldArray({ control, name: path });
  const labelId = useId();
  return (
    <FieldShell path={path} label={label} description={description} labelId={labelId}>
      <div className="space-y-2" role="group" aria-labelledby={labelId}>
        {fields.map((field, index) => (
          <div key={field.id} className="flex items-center gap-2">
            <Input
              type="number"
              step="any"
              aria-label={`${label} ${index + 1}`}
              {...register(`${path}.${index}`, {
                setValueAs: (value: unknown) => Number(value),
              })}
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
          onClick={() => append(0)}
        >
          <Plus className="mr-1 h-4 w-4" />
          {addLabel}
        </Button>
      </div>
    </FieldShell>
  );
}
