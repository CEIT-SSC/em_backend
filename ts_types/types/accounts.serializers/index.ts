export enum GrantTypeChoiceEnum {
    PASSWORD = 'password',
    REFRESH_TOKEN = 'refresh_token',
}

export enum GrantTypeChoiceEnumValues {
    password = 'password',
    refresh_token = 'refresh_token',
}

export enum TokenTypeHintChoiceEnum {
    ACCESS_TOKEN = 'access_token',
    REFRESH_TOKEN = 'refresh_token',
}

export enum TokenTypeHintChoiceEnumValues {
    access_token = 'access_token',
    refresh_token = 'refresh_token',
}


export interface AuthorizationForm {
    username: string;
    password: string;
    clientId: string;
    /**
    * @format url
    */
    redirectUri: string;
    responseType: string;
    scope?: string;
    codeChallenge?: string;
    codeChallengeMethod?: string;
    allow: string;
}

export interface ChangePassword {
    oldPassword: string;
    newPassword: string;
}

export interface EmailVerification {
    /**
    * @format email
    */
    email: string;
    /**
    * @minLength 6
    * @maxLength 6
    */
    code: string;
}

export interface HandshakeToken {
    handshakeToken: string;
}

export interface RefreshToken {
    refreshToken: string;
}

export interface ResendVerificationEmail {
    /**
    * @format email
    */
    email: string;
}

export interface RevokeTokenRequest {
    token: string;
    clientId: string;
    clientSecret?: string;
    tokenTypeHint?: TokenTypeHintChoiceEnum;
}

export interface SimpleForgotPassword {
    /**
    * @format email
    */
    email: string;
}

export interface SocialLogin {
    accessToken?: string;
    code?: string;
    idToken?: string;
}

export interface Staff {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @maxLength 200
    */
    name: string;
    /**
    * @maxLength 200
    */
    team?: string | null;
    /**
    * @maxLength 100
    */
    role: string;
    description: string;
    picture: File;
    /**
    * @maxLength 300
    * @format url
    */
    socialAccountLink?: string | null;
    /**
    * @maxLength 300
    * @format url
    */
    GithubLink?: string | null;
}

export interface TokenRequest {
    grantType: GrantTypeChoiceEnum;
    username?: string;
    password?: string;
    refreshToken?: string;
    clientId: string;
    clientSecret?: string;
}

export interface Token {
    accessToken: string;
    expiresIn: number;
    tokenType: string;
    scope: string;
    refreshToken: string;
}

export interface UserProfile {
    /**
    * @label Email Address
    * @format email
    */
    email?: string;
    /**
    * @label First Name
    * @maxLength 150
    */
    firstName?: string;
    /**
    * @label Last Name
    * @maxLength 150
    */
    lastName?: string;
    /**
    * @label Phone Number
    * @maxLength 20
    */
    phoneNumber?: string | null;
    /**
    * @label Profile Picture
    */
    profilePicture?: File | null;
    /**
    * @label Date Joined
    * @format date-time
    */
    dateJoined?: string;
}

export interface UserProfileUpdate {
    /**
    * @label First Name
    * @maxLength 150
    */
    firstName?: string;
    /**
    * @label Last Name
    * @maxLength 150
    */
    lastName?: string;
    /**
    * @maxLength 20
    */
    phoneNumber?: string;
    /**
    * @label Profile Picture
    */
    profilePicture?: File | null;
}

export interface UserRegistration {
    /**
    * @label Email Address
    * @maxLength 254
    * @format email
    */
    email: string;
    password: string;
    /**
    * @label First Name
    * @maxLength 150
    * @default ""
    */
    firstName?: string;
    /**
    * @label Last Name
    * @maxLength 150
    * @default ""
    */
    lastName?: string;
    /**
    * @maxLength 20
    */
    phoneNumber: string;
}

export interface UserRegistrationSuccess {
    /**
    * @format email
    */
    email: string;
}

