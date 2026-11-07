import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
  useLocation,
} from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'

import { Redirect } from './Redirect'

// Like AppShell: the guard re-renders on every location change, including the pending
// navigation it starts. With TanStack's <Navigate> that looped forever.
function Guarded() {
  useLocation()
  return <Redirect to="sign-in" />
}

test('a signed-out visit goes to sign-in once, keeping where it was headed', async () => {
  const root = createRootRoute({ component: Outlet })
  const login = createRoute({
    getParentRoute: () => root,
    path: '/login',
    component: () => <p>Sign in</p>,
  })
  const consent = createRoute({
    getParentRoute: () => root,
    path: '/oauth/consent',
    component: Guarded,
  })
  const history = createMemoryHistory({ initialEntries: ['/oauth/consent?request=abc'] })
  const router = createRouter({ routeTree: root.addChildren([login, consent]), history })
  let navigations = 0
  router.subscribe('onBeforeNavigate', () => navigations++)

  render(<RouterProvider router={router} />)

  expect(await screen.findByText('Sign in')).toBeInTheDocument()
  expect(router.state.location.pathname).toBe('/login')
  expect(router.state.location.search).toEqual({ redirect: '/oauth/consent?request=abc' })
  expect(navigations).toBeLessThanOrEqual(2) // the initial load, then the one redirect
})
