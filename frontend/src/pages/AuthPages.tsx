import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { Link, useLocation, useNavigate } from 'react-router'
import { z } from 'zod'
import { isApiError } from '../api/client'
import { useLogin, useRegister } from '../api/hooks'
import { Button, Field } from '../components/ui'

const loginSchema = z.object({
  email: z.email('Enter a valid email address.'),
  password: z.string().min(1, 'Enter your password.').max(256, 'Passwords are at most 256 characters.'),
})

const registerSchema = z.object({
  email: z.email('Enter a valid email address.'),
  password: z
    .string()
    .min(8, 'Use at least 8 characters.')
    .max(256, 'Use at most 256 characters.'),
})

type Credentials = z.infer<typeof loginSchema>

function useReturnTo(): string {
  const state = useLocation().state as { from?: string } | null
  return state?.from && state.from.startsWith('/') ? state.from : '/'
}

function CredentialsFields({
  register,
  errors,
  passwordHint,
  passwordAutocomplete,
}: {
  register: ReturnType<typeof useForm<Credentials>>['register']
  errors: ReturnType<typeof useForm<Credentials>>['formState']['errors']
  passwordHint?: string
  passwordAutocomplete: 'current-password' | 'new-password'
}) {
  return (
    <>
      <Field label="Email" error={errors.email?.message}>
        {(props) => (
          <input className="input" type="email" autoComplete="email" {...props} {...register('email')} />
        )}
      </Field>
      <Field label="Password" error={errors.password?.message} hint={passwordHint}>
        {(props) => (
          <input
            className="input"
            type="password"
            autoComplete={passwordAutocomplete}
            {...props}
            {...register('password')}
          />
        )}
      </Field>
    </>
  )
}

export function LoginPage() {
  const navigate = useNavigate()
  const returnTo = useReturnTo()
  const login = useLogin()
  const form = useForm<Credentials>({ resolver: zodResolver(loginSchema) })

  const onSubmit = form.handleSubmit((values) =>
    login.mutate(values, { onSuccess: () => navigate(returnTo, { replace: true }) }),
  )

  return (
    <main className="auth">
      <form className="auth-card form" onSubmit={onSubmit} noValidate>
        <h1>Sign in to Gate</h1>
        {login.isError && (
          <p className="alert" role="alert">
            {isApiError(login.error, 401)
              ? 'Email or password is incorrect.'
              : 'Sign-in failed. Try again in a moment.'}
          </p>
        )}
        <CredentialsFields
          register={form.register}
          errors={form.formState.errors}
          passwordAutocomplete="current-password"
        />
        <Button type="submit" variant="primary" disabled={login.isPending}>
          {login.isPending ? 'Signing in…' : 'Sign in'}
        </Button>
        <p className="muted">
          No account? <Link to="/register">Create one</Link>
        </p>
      </form>
    </main>
  )
}

export function RegisterPage() {
  const navigate = useNavigate()
  const registration = useRegister()
  const form = useForm<Credentials>({ resolver: zodResolver(registerSchema) })

  const onSubmit = form.handleSubmit((values) =>
    registration.mutate(values, {
      onSuccess: () => navigate('/', { replace: true }),
      onError: (error) => {
        if (isApiError(error, 409)) {
          form.setError('email', { message: 'An account with this email already exists.' })
        }
      },
    }),
  )

  const unexpected = registration.isError && !isApiError(registration.error, 409)

  return (
    <main className="auth">
      <form className="auth-card form" onSubmit={onSubmit} noValidate>
        <h1>Create your Gate account</h1>
        {unexpected && (
          <p className="alert" role="alert">
            Registration failed. Try again in a moment.
          </p>
        )}
        <CredentialsFields
          register={form.register}
          errors={form.formState.errors}
          passwordHint="At least 8 characters."
          passwordAutocomplete="new-password"
        />
        <Button type="submit" variant="primary" disabled={registration.isPending}>
          {registration.isPending ? 'Creating account…' : 'Create account'}
        </Button>
        <p className="muted">
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </form>
    </main>
  )
}
