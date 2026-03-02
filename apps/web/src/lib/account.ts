import { apiFetch } from '@/lib/auth';

type DeleteAccountPayload = {
  password: string;
};

export async function deleteAccount(payload: DeleteAccountPayload): Promise<void> {
  await apiFetch<unknown>('/api/v1/account', {
    method: 'DELETE',
    body: {
      password: payload.password
    }
  });
}
