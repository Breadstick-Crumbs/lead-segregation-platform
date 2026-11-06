import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import {
  getListAssignableUsersQueryKey,
  getListUsersQueryKey,
  useCreateUser,
  useListUsers,
  useUpdateUser,
  type Role,
  type UserRead,
} from '../../api/generated'
import {
  Button,
  Card,
  Chip,
  ErrorState,
  SelectField,
  Skeleton,
  TextField,
} from '../../components/ui'
import { timeAgo } from '../../lib/format'
import { useSession } from '../auth/useSession'

function useRefreshUsers() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: getListUsersQueryKey() })
    void queryClient.invalidateQueries({ queryKey: getListAssignableUsersQueryKey() })
  }
}

export function UserSettings() {
  const users = useListUsers()
  const [adding, setAdding] = useState(false)
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-body">
          Sales reps get leads by round-robin. Admins also manage settings and reassign leads. A
          user without a password signs in with Microsoft.
        </p>
        {!adding ? (
          <Button icon="plus" onClick={() => setAdding(true)}>
            Add user
          </Button>
        ) : null}
      </div>
      {adding ? <AddUser onDone={() => setAdding(false)} /> : null}
      {users.isPending ? (
        <Skeleton className="h-48 rounded-card" />
      ) : users.isError ? (
        <ErrorState error={users.error} onRetry={() => void users.refetch()} />
      ) : (
        <ul className="flex flex-col divide-y divide-border rounded-card border border-border bg-surface">
          {users.data.map((u) => (
            <UserRow key={u.id} user={u} />
          ))}
        </ul>
      )}
    </div>
  )
}

function UserRow({ user }: { user: UserRead }) {
  const { user: me } = useSession()
  const refresh = useRefreshUsers()
  const update = useUpdateUser({ mutation: { onSuccess: refresh } })
  const [error, setError] = useState<string | null>(null)
  const change = async (data: { role?: Role; is_active?: boolean }) => {
    setError(null)
    try {
      await update.mutateAsync({ userId: user.id, data })
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't update the user")
    }
  }
  return (
    <li className="flex flex-wrap items-center gap-4 px-5 py-4">
      <div className="min-w-48 flex-1">
        <p className="font-semibold text-ink">
          {user.full_name}{' '}
          {user.id === me?.id ? (
            <span className="text-sm font-normal text-muted">(you)</span>
          ) : null}
        </p>
        <p className="text-sm text-muted">{user.email}</p>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {user.microsoft_linked ? <Chip tone="accent">Microsoft</Chip> : null}
        {!user.has_password ? <Chip>No password</Chip> : null}
        {!user.is_active ? <Chip tone="low">Deactivated</Chip> : null}
      </div>
      <p className="w-36 font-mono text-xs text-muted">
        Last sign-in {timeAgo(user.last_login_at)}
      </p>
      <label className="flex items-center gap-2">
        <span className="sr-only">Role for {user.full_name}</span>
        <select
          value={user.role}
          disabled={update.isPending}
          onChange={(e) => void change({ role: e.target.value as Role })}
          className="min-h-11 rounded-control border border-border bg-surface px-3 text-ink"
        >
          <option value="sales_rep">Sales rep</option>
          <option value="admin">Admin</option>
        </select>
      </label>
      <Button
        variant={user.is_active ? 'danger' : 'secondary'}
        size="sm"
        busy={update.isPending}
        disabled={user.id === me?.id}
        onClick={() => void change({ is_active: !user.is_active })}
      >
        {user.is_active ? 'Deactivate' : 'Reactivate'}
      </Button>
      {error ? (
        <p role="alert" className="w-full text-sm text-priority-critical">
          {error}
        </p>
      ) : null}
    </li>
  )
}

function AddUser({ onDone }: { onDone: () => void }) {
  const refresh = useRefreshUsers()
  const create = useCreateUser({ mutation: { onSuccess: refresh } })
  const [form, setForm] = useState({
    full_name: '',
    email: '',
    role: 'sales_rep' as Role,
    password: '',
  })
  const [errors, setErrors] = useState<Record<string, string>>({})

  async function submit(event: FormEvent) {
    event.preventDefault()
    const next: Record<string, string> = {}
    if (!form.full_name.trim()) next.full_name = 'Enter their name'
    if (!/^\S+@\S+\.\S+$/.test(form.email)) next.email = 'Enter their work email'
    if (form.password && form.password.length < 10)
      next.password = 'At least 10 characters, or leave empty for Microsoft-only'
    setErrors(next)
    if (Object.keys(next).length) return
    try {
      await create.mutateAsync({
        data: {
          full_name: form.full_name.trim(),
          email: form.email.trim(),
          role: form.role,
          password: form.password || null,
        },
      })
      onDone()
    } catch (error) {
      setErrors({ form: error instanceof Error ? error.message : "Couldn't add the user" })
    }
  }

  return (
    <Card>
      <form noValidate onSubmit={submit} className="flex flex-col gap-4">
        <h2 className="text-lg font-bold text-ink">Add a user</h2>
        {errors.form ? (
          <p role="alert" className="text-sm text-priority-critical">
            {errors.form}
          </p>
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Full name"
            value={form.full_name}
            onChange={(e) => setForm({ ...form, full_name: e.target.value })}
            error={errors.full_name}
          />
          <TextField
            label="Work email"
            type="email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            error={errors.email}
          />
          <SelectField
            label="Role"
            value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value as Role })}
          >
            <option value="sales_rep">Sales rep</option>
            <option value="admin">Admin</option>
          </SelectField>
          <TextField
            label="Password"
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            error={errors.password}
            hint="Optional: leave empty if they'll sign in with Microsoft"
          />
        </div>
        <div className="flex gap-2">
          <Button type="submit" busy={create.isPending}>
            Add user
          </Button>
          <Button type="button" variant="ghost" onClick={onDone}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  )
}
